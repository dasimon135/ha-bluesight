#include "bluesight.h"

#ifdef USE_ESP32

#include <cstdio>
#include <cstring>
#include <vector>

#include "esphome/core/hal.h"
#include "esphome/core/log.h"

namespace esphome::bluesight {

static const char *const TAG = "bluesight";

/// Room for "aabbccddeeff" plus its terminator.
static constexpr size_t ADDRESS_BUFFER_SIZE = 2 * ESP_BD_ADDR_LEN + 1;
/// Room for "aabbccddeeff:4294967.2" -- the longest field either mapping emits.
static constexpr size_t FIELD_BUFFER_SIZE = 32;

/// Format a BLE address as exactly twelve lowercase hex characters.
///
/// Deliberately not `format_hex_pretty()`, which renders `D0.CF.13.0E.C9.2A`:
/// dot-separated, which the integration's address parser rejects. It is the
/// obvious-looking helper and it is the wrong one, and the failure it causes is
/// not a visible error -- the field is silently dropped, and a proxy holding a
/// full bond list ends up asserting that it holds none.
///
/// Plain `snprintf` rather than an ESPHome helper because this one line is what
/// the whole data contract rests on, and libc's `%02x` is the same in every
/// ESPHome release.
static void format_address(const uint8_t *address, char *out, size_t out_len) {
  snprintf(out, out_len, "%02x%02x%02x%02x%02x%02x", address[0], address[1], address[2], address[3],
           address[4], address[5]);
}

/// Append one field to a comma-separated payload.
static void append_field(std::string &payload, const char *field) {
  if (!payload.empty()) {
    payload.push_back(',');
  }
  payload.append(field);
}

void BlueSightComponent::setup() {
  ESP_LOGCONFIG(TAG, "Setting up BlueSight telemetry (read-only observer)");

  // Publish the two counters that are genuinely empty at boot straight away, so
  // a healthy proxy reads as "reporting, zero entries" rather than sitting at
  // `unknown` -- which the integration reads as "no telemetry at all" -- for a
  // whole update interval.
  this->publish_smp_failures_();
  this->publish_slots_();

  // Bonds cannot be read yet: `esp_ble_get_bond_device_num()` fails until
  // Bluedroid is enabled, and esp32_ble only enables it from its `loop()`.
  // A short timeout gets the first real bond list out well before the first
  // periodic tick, without guessing at component ordering.
  this->set_timeout(10000, [this]() { this->publish_bonds_(); });
}

void BlueSightComponent::update() {
  // Slots and SMP counters publish the moment they change; this tick exists so
  // an idle time keeps advancing while nothing at all is happening, and so a
  // bond added or removed out of band is eventually noticed.
  this->publish_smp_failures_();
  this->publish_bonds_();
  this->publish_slots_();
}

void BlueSightComponent::dump_config() {
  ESP_LOGCONFIG(TAG, "BlueSight:");
  ESP_LOGCONFIG(TAG,
                "  Read-only: opens no connection, writes no bond, never calls bluetooth_proxy");
#ifndef USE_ESP32_BLE_CLIENT
  ESP_LOGCONFIG(TAG, "  GATT connection slots: not observable (no BLE client stack)");
#endif
  LOG_UPDATE_INTERVAL(this);
  LOG_TEXT_SENSOR("  ", "SMP failures", this->smp_failures_sensor_);
  LOG_TEXT_SENSOR("  ", "Bonds", this->bonds_sensor_);
  LOG_TEXT_SENSOR("  ", "Slots", this->slots_sensor_);
}

void BlueSightComponent::publish_if_changed_(text_sensor::TextSensor *sensor,
                                             const std::string &value) {
  if (sensor == nullptr) {
    return;
  }
  if (sensor->has_state() && sensor->state == value) {
    return;
  }
  sensor->publish_state(value);
}

// --- SMP failures ------------------------------------------------------------

void BlueSightComponent::gap_event_handler(esp_gap_ble_cb_event_t event,
                                           esp_ble_gap_cb_param_t *param) {
  // Dispatched from esp32_ble's `loop()`, not from the Bluetooth task: the
  // event was copied into a queue first, so publishing from here is safe and
  // `param` is a stable copy for the duration of this call.
  if (event != ESP_GAP_BLE_AUTH_CMPL_EVT || param == nullptr) {
    return;
  }
  const esp_ble_auth_cmpl_t &auth = param->ble_security.auth_cmpl;
  if (auth.success) {
    return;
  }

  this->record_smp_failure_(auth.bd_addr);

  char address[ADDRESS_BUFFER_SIZE];
  format_address(auth.bd_addr, address, sizeof(address));
  ESP_LOGD(TAG, "SMP failure for %s (reason 0x%02x)", address, (unsigned) auth.fail_reason);

  // The reason is logged and not published: it is per-event, and the wire
  // format carries a monotonic count. Turning a reason into a verdict is the
  // integration's job, where it can be retuned without a reflash.
  this->publish_smp_failures_();
}

void BlueSightComponent::record_smp_failure_(const uint8_t *address) {
  const uint32_t now = millis();

  for (SmpFailureEntry &entry : this->smp_failures_) {
    if (entry.used && memcmp(entry.address, address, ESP_BD_ADDR_LEN) == 0) {
      // Saturate rather than wrap. A wrap looks exactly like a reboot to the
      // integration's delta tracker, which rearms its baseline and counts
      // nothing -- an under-count, which is the safe direction, but 2^32
      // failures is not a number anyone reaches and a stuck ceiling reads
      // more clearly in a log than a sudden zero.
      if (entry.failures < UINT32_MAX) {
        entry.failures++;
      }
      entry.updated_ms = now;
      return;
    }
  }

  SmpFailureEntry *victim = nullptr;
  for (SmpFailureEntry &entry : this->smp_failures_) {
    if (!entry.used) {
      victim = &entry;
      break;
    }
  }

  if (victim == nullptr) {
    // Every slot is taken: drop the least recently updated one. The age is
    // computed in uint32_t so it stays correct across the 49.7-day millis()
    // rollover, where a signed difference would go negative and pick the
    // wrong victim.
    uint32_t oldest_age = 0;
    for (SmpFailureEntry &entry : this->smp_failures_) {
      const uint32_t age = now - entry.updated_ms;
      if (victim == nullptr || age > oldest_age) {
        victim = &entry;
        oldest_age = age;
      }
    }
    char evicted[ADDRESS_BUFFER_SIZE];
    format_address(victim->address, evicted, sizeof(evicted));
    ESP_LOGW(TAG,
             "SMP failure table full (%u addresses); dropping %s, whose count was %u. "
             "Home Assistant re-baselines a dropped address rather than counting it twice.",
             (unsigned) MAX_SMP_ADDRESSES, evicted, (unsigned) victim->failures);
  }

  memcpy(victim->address, address, ESP_BD_ADDR_LEN);
  victim->failures = 1;
  victim->updated_ms = now;
  victim->used = true;
}

void BlueSightComponent::publish_smp_failures_() {
  if (this->smp_failures_sensor_ == nullptr) {
    return;
  }
  std::string payload;
  char address[ADDRESS_BUFFER_SIZE];
  char field[FIELD_BUFFER_SIZE];
  for (const SmpFailureEntry &entry : this->smp_failures_) {
    if (!entry.used) {
      continue;
    }
    format_address(entry.address, address, sizeof(address));
    // "%u" and nothing else: no sign, no separator, no exponent. The parser
    // rejects every other spelling of a number.
    snprintf(field, sizeof(field), "%s:%u", address, (unsigned) entry.failures);
    append_field(payload, field);
  }
  // An empty payload here is a statement, not a gap: "reporting, zero
  // failures". The integration distinguishes that from `unknown`.
  this->publish_if_changed_(this->smp_failures_sensor_, payload);
}

// --- Bonds -------------------------------------------------------------------

void BlueSightComponent::publish_bonds_() {
  if (this->bonds_sensor_ == nullptr) {
    return;
  }

  const int total = esp_ble_get_bond_device_num();
  if (total < 0) {
    // Bluedroid is not enabled yet (or at all). Publishing nothing leaves the
    // sensor `unknown`, which the integration reads as "no telemetry" --
    // correct. Publishing "" would claim this proxy holds no bonds.
    ESP_LOGD(TAG, "Bond store not readable yet; leaving the bond list unreported");
    return;
  }
  if (total == 0) {
    this->publish_if_changed_(this->bonds_sensor_, "");
    return;
  }
  if (total > MAX_REPORTED_BONDS) {
    // Refuse rather than truncate. A truncated bond list is worse than no bond
    // list: an address that is bonded but cut off looks unbonded, which is
    // exactly the input that makes the integration's BOND_LOST detector fire
    // on a healthy proxy. Unreported is honest; half-reported is a false
    // accusation. Only reachable with a non-default CONFIG_BT_SMP_MAX_BONDS.
    ESP_LOGW(TAG,
             "%d bonds stored but at most %u fit in a Home Assistant entity state; "
             "leaving the bond list unreported rather than truncating it",
             total, (unsigned) MAX_REPORTED_BONDS);
    return;
  }

  // Heap rather than stack: esp_ble_bond_dev_t carries the full key material,
  // and fifteen of them is more than an ESPHome loop task wants to borrow.
  std::vector<esp_ble_bond_dev_t> devices(static_cast<size_t>(total));
  int count = total;
  if (esp_ble_get_bond_device_list(&count, devices.data()) != ESP_OK) {
    ESP_LOGW(TAG, "Reading the bond list failed; leaving it unreported");
    return;
  }
  if (count < 0 || static_cast<size_t>(count) > devices.size()) {
    // ESP-IDF only ever clamps `count` down to the stored total, so this cannot
    // happen -- but the value indexes a buffer, so it is checked rather than
    // trusted.
    ESP_LOGW(TAG, "Bond list returned %d entries for a %u-entry buffer; ignoring", count,
             (unsigned) devices.size());
    return;
  }

  std::string payload;
  char address[ADDRESS_BUFFER_SIZE];
  for (int i = 0; i < count; i++) {
    format_address(devices[i].bd_addr, address, sizeof(address));
    append_field(payload, address);
  }
  this->publish_if_changed_(this->bonds_sensor_, payload);
}

// --- GATT connection slots ---------------------------------------------------

void BlueSightComponent::publish_slots_() {
  if (this->slots_sensor_ == nullptr) {
    return;
  }
#ifdef USE_ESP32_BLE_CLIENT
  const uint32_t now = millis();
  std::string payload;
  char address[ADDRESS_BUFFER_SIZE];
  char field[FIELD_BUFFER_SIZE];
  for (const SlotEntry &entry : this->slots_) {
    if (!entry.used) {
      continue;
    }
    format_address(entry.address, address, sizeof(address));
    // Everything stays in uint32_t until it is printed. Subtracting in
    // uint32_t is correct across the 49.7-day millis() rollover, where an
    // int32_t difference goes negative -- and a negative duration is rejected
    // outright by the parser. Printing the tenths separately keeps floating
    // point out of it entirely, so no value can ever render as "1e+06": "%g"
    // switches to exponent form at 1e6 seconds, which is 11.6 days and well
    // within reach of a bonded slot that never sees traffic.
    const uint32_t idle_ms = now - entry.last_traffic_ms;
    snprintf(field, sizeof(field), "%s:%u.%u", address, (unsigned) (idle_ms / 1000u),
             (unsigned) (idle_ms % 1000u / 100u));
    append_field(payload, field);
  }
  this->publish_if_changed_(this->slots_sensor_, payload);
#else
  // Unreachable: codegen creates no slots sensor without the BLE client stack.
  this->publish_if_changed_(this->slots_sensor_, "");
#endif
}

#ifdef USE_ESP32_BLE_CLIENT

void BlueSightComponent::gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                                             esp_ble_gattc_cb_param_t *param) {
  if (param == nullptr) {
    return;
  }
  // conn_id is only unique within one GATT client interface, and every
  // registered client's events arrive here, so the interface is part of the
  // key. Read and write events carry no address at all -- only a conn_id --
  // which is why the mapping has to be built when the connection opens and
  // torn down when it closes rather than derived per event.
  const uint32_t interface = static_cast<uint32_t>(gattc_if) << 16;
  bool membership_changed = false;

  switch (event) {
    case ESP_GATTC_CONNECT_EVT:
      // Deliberately not tracked. CONNECT reports the *physical* ACL link and
      // ESP-IDF delivers it to every registered GATT client application, each
      // under its own `gattc_if`. Opening a slot here therefore recorded one
      // link as N slots -- one per registered client, which on a proxy is its
      // connection slots plus every `ble_client` -- all carrying the same
      // address. Only the interface that actually owns the connection then
      // received the traffic events, so the other N-1 records sat frozen at
      // the moment the link came up and aged without bound: a manufactured
      // ghost slot, and a table full of phantoms that crowded out real ones.
      //
      // OPEN below is the per-application event and the one that means "this
      // client holds a GATT connection", which is the quantity being reported.
      // It follows CONNECT within milliseconds for the owning interface, so
      // nothing real is lost by ignoring this.
      return;
    case ESP_GATTC_OPEN_EVT:
      if (param->open.status == ESP_GATT_OK) {
        membership_changed = true;
        this->open_slot_(interface | param->open.conn_id, param->open.remote_bda);
      }
      break;
    case ESP_GATTC_DISCONNECT_EVT:
      // Closed by address, not by key: DISCONNECT is broadcast like CONNECT,
      // so the copy that reaches the owning interface is not distinguishable
      // here, and the link is gone for every record regardless of which
      // interface opened it. Idempotent, and it cannot leave a slot behind if
      // a CLOSE is ever missed.
      membership_changed = true;
      this->close_slots_for_address_(param->disconnect.remote_bda);
      break;
    case ESP_GATTC_CLOSE_EVT:
      membership_changed = true;
      this->close_slot_(interface | param->close.conn_id);
      break;
    case ESP_GATTC_NOTIFY_EVT:
      this->touch_slot_(interface | param->notify.conn_id);
      break;
    case ESP_GATTC_READ_CHAR_EVT:
    case ESP_GATTC_READ_DESCR_EVT:
      this->touch_slot_(interface | param->read.conn_id);
      break;
    case ESP_GATTC_WRITE_CHAR_EVT:
    case ESP_GATTC_WRITE_DESCR_EVT:
      this->touch_slot_(interface | param->write.conn_id);
      break;
    default:
      return;
  }

  // Publish when the set of slots changes, not when traffic merely resets an
  // idle timer: a chatty notifying device would otherwise push a new state over
  // the API several times a second, for a value the periodic tick reports just
  // as truthfully.
  if (membership_changed) {
    this->publish_slots_();
  }
}

void BlueSightComponent::open_slot_(uint32_t key, const uint8_t *address) {
  const uint32_t now = millis();

  for (SlotEntry &entry : this->slots_) {
    if (entry.used && entry.key == key) {
      // A client that reopens the same conn_id without an intervening close is
      // updating the record it already has, not taking a second slot.
      memcpy(entry.address, address, ESP_BD_ADDR_LEN);
      entry.last_traffic_ms = now;
      return;
    }
  }
  for (SlotEntry &entry : this->slots_) {
    if (!entry.used) {
      entry.key = key;
      memcpy(entry.address, address, ESP_BD_ADDR_LEN);
      entry.last_traffic_ms = now;
      entry.used = true;
      return;
    }
  }

  // Unreachable in any configuration ESPHome accepts: the table is sized to
  // ESP-IDF's connection ceiling. Reported rather than silently dropped,
  // because the symptom would otherwise be a slot that never appears.
  char unreported[ADDRESS_BUFFER_SIZE];
  format_address(address, unreported, sizeof(unreported));
  ESP_LOGW(TAG, "More than %u concurrent connections; %s is not being tracked",
           (unsigned) MAX_TRACKED_SLOTS, unreported);
}

void BlueSightComponent::close_slot_(uint32_t key) {
  for (SlotEntry &entry : this->slots_) {
    if (entry.used && entry.key == key) {
      entry.used = false;
      return;
    }
  }
}

void BlueSightComponent::close_slots_for_address_(const uint8_t *address) {
  // No early return: one address is one physical link, but a client that
  // reconnected without a clean close could still hold a stale record for it,
  // and the link being down makes every one of them false.
  for (SlotEntry &entry : this->slots_) {
    if (entry.used && memcmp(entry.address, address, ESP_BD_ADDR_LEN) == 0) {
      entry.used = false;
    }
  }
}

void BlueSightComponent::touch_slot_(uint32_t key) {
  const uint32_t now = millis();
  for (SlotEntry &entry : this->slots_) {
    if (entry.used && entry.key == key) {
      entry.last_traffic_ms = now;
      return;
    }
  }
}

#endif  // USE_ESP32_BLE_CLIENT

}  // namespace esphome::bluesight

#endif  // USE_ESP32
