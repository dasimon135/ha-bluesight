#pragma once

#include "esphome/core/defines.h"  // must precede the conditional includes below

#ifdef USE_ESP32

#include <cstdint>
#include <string>

#include "esphome/components/text_sensor/text_sensor.h"
#include "esphome/core/component.h"

#include <esp_bt_defs.h>
#include <esp_gap_ble_api.h>

#ifdef USE_ESP32_BLE_CLIENT
#include <esp_gattc_api.h>
#endif

namespace esphome::bluesight {

/// Distinct addresses whose SMP failures are counted.
///
/// The cap is a wire-format constraint, not a memory one: Home Assistant
/// rejects an entity state longer than 255 characters, and 8 entries of
/// "aabbccddeeff:4294967295" (23 chars) plus separators is 191. When a ninth
/// address appears the least recently updated entry is dropped.
static constexpr uint8_t MAX_SMP_ADDRESSES = 8;

/// Concurrent GATT client connections tracked.
///
/// Bluedroid's own ceiling: esp32_ble's `max_connections` is range-checked
/// against ESP-IDF's maximum of 9, so this array can never overflow in a
/// configuration ESPHome will accept. 9 entries of "aabbccddeeff:4294967.2"
/// (22 chars) plus separators is 206.
static constexpr uint8_t MAX_TRACKED_SLOTS = 9;

/// Bonds reported. `CONFIG_BT_SMP_MAX_BONDS` defaults to 15 (range 1..32), and
/// 15 * 13 - 1 = 194 characters fits the state cap. A store configured beyond
/// this is *not* truncated -- see `publish_bonds_`.
static constexpr uint8_t MAX_REPORTED_BONDS = 15;

struct SmpFailureEntry {
  esp_bd_addr_t address;
  uint32_t failures;
  uint32_t updated_ms;
  bool used;
};

#ifdef USE_ESP32_BLE_CLIENT
struct SlotEntry {
  esp_bd_addr_t address;
  /// (gattc_if << 16) | conn_id. ESPHome fans GATTC events out from every
  /// registered application, and `read`/`write` events carry only a conn_id,
  /// so the interface has to be part of the key for the lookup to be
  /// unambiguous across clients.
  ///
  /// That makes the key per-*application*, which is why only the per-
  /// application events may create an entry. The broadcast link events
  /// (CONNECT, DISCONNECT) arrive once per registered client for a single
  /// physical link, so keying those by interface records one connection N
  /// times. See the CONNECT case in `gattc_event_handler`.
  uint32_t key;
  uint32_t last_traffic_ms;
  bool used;
};
#endif

/// Passive observer on the proxy's BLE event stream.
///
/// Registered as a GAP and GATTC event handler through `esp32_ble`, which
/// dispatches every event to every handler. Nothing here initiates: no
/// connection is opened, no bond is written or removed, and `bluetooth_proxy`
/// is never called. BlueSight is read-only diagnostics and that invariant
/// holds into the firmware.
///
/// It also holds no threshold and reaches no verdict. It counts events and
/// formats three strings; the Home Assistant side decides what they mean.
class BlueSightComponent : public PollingComponent {
 public:
  void setup() override;
  void update() override;
  void dump_config() override;
  float get_setup_priority() const override { return setup_priority::AFTER_BLUETOOTH; }

  /// Called by the lambda `esp32_ble.register_gap_event_handler` generates.
  /// Not an override: ESPHome 2026.8 replaced the `GAPEventHandler` base class
  /// with a static callback manager, so the binding is by name and signature.
  void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param);

#ifdef USE_ESP32_BLE_CLIENT
  void gattc_event_handler(esp_gattc_cb_event_t event, esp_gatt_if_t gattc_if,
                           esp_ble_gattc_cb_param_t *param);
#endif

  void set_smp_failures_text_sensor(text_sensor::TextSensor *text_sensor) {
    this->smp_failures_sensor_ = text_sensor;
  }
  void set_bonds_text_sensor(text_sensor::TextSensor *text_sensor) {
    this->bonds_sensor_ = text_sensor;
  }
  void set_slots_text_sensor(text_sensor::TextSensor *text_sensor) {
    this->slots_sensor_ = text_sensor;
  }

 protected:
  void record_smp_failure_(const uint8_t *address);
  void publish_smp_failures_();
  void publish_bonds_();
  void publish_slots_();
  /// Publish only when the string actually changed, so a proxy that notifies
  /// every second does not push an identical state over the API every second.
  static void publish_if_changed_(text_sensor::TextSensor *sensor, const std::string &value);

#ifdef USE_ESP32_BLE_CLIENT
  void open_slot_(uint32_t key, const uint8_t *address);
  void close_slot_(uint32_t key);
  void close_slots_for_address_(const uint8_t *address);
  void touch_slot_(uint32_t key);
  SlotEntry slots_[MAX_TRACKED_SLOTS]{};
#endif

  SmpFailureEntry smp_failures_[MAX_SMP_ADDRESSES]{};

  text_sensor::TextSensor *smp_failures_sensor_{nullptr};
  text_sensor::TextSensor *bonds_sensor_{nullptr};
  text_sensor::TextSensor *slots_sensor_{nullptr};
};

}  // namespace esphome::bluesight

#endif  // USE_ESP32
