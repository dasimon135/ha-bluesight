# [Intégration] BlueSight — voir ce qui bloque vraiment vos proxys Bluetooth

> **Message de lancement du fil HACF, à jour pour la v0.3.1.**
> Le projet n'avait jamais été annoncé jusque-là.

Bonjour à tous 👋

Je partage une intégration custom que j'ai écrite pour un problème qui m'a fait perdre pas mal de temps, et sur lequel je n'ai trouvé aucun outil.

Elle s'appelle `bluesight`.

## Le problème

Le Bluetooth de Home Assistant a deux étages, et on n'en voit qu'un.

Le premier, c'est **ce que chaque proxy entend** : quels appareils passent à portée. Celui-là est bien couvert depuis HA 2025.2 par le *Moniteur d'annonces* (Paramètres → Appareils → Bluetooth → Configurer). On y voit très bien quel proxy capte quel appareil.

Le second, c'est **le nombre de connexions qu'un proxy peut tenir en même temps**. Un ESP32 en tient trois par défaut. Ces quelques places — les « slots » — sont une ressource partagée, et elles sont totalement invisibles. Aucun écran dans Home Assistant ne vous dit qui les occupe.

Et c'est là que ça casse. Quand une place reste bloquée par un appareil qui ne répond plus, elle n'est jamais rendue. Vos autres appareils Bluetooth passent en `indisponible`, sans erreur, sans message, sans rien. Vous redémarrez le proxy au hasard, ça remarche un moment, ça recommence. 😐

## Ce que fait BlueSight

Il lit les allocations de slots que Home Assistant tient déjà en interne, et il en tire six diagnostics.

**Côté appareils :**

- **Blocage (« deadlock »)** — la même adresse Bluetooth est réservée sur **deux proxys ou plus** en même temps. C'est impossible physiquement : un appareil Bluetooth ne parle qu'à un seul interlocuteur. Donc les places en trop sont des reliquats qui ne seront jamais rendus. C'est un vrai bug documenté du cœur de HA ([issue #176516](https://github.com/home-assistant/core/issues/176516)), et la seule méthode proposée dans le fil, c'est d'activer les logs debug et de les lire à la main.
- **Slot fantôme** — un proxy réserve toujours une place pour un appareil dont **toutes** les entités sont passées en `indisponible`. La place est dépensée pour rien.
- **Tempête d'appairage** — un appareil enchaîne les échecs de connexion en rafale, ce qui secoue le proxy et monopolise les places.

**Côté proxys eux-mêmes :**

- **Proxy hors ligne** — il a disparu du bus. Vérifiez son alimentation et son Wi-Fi.
- **Proxy muet** — il est toujours en ligne mais n'entend plus rien depuis un moment. Il est allumé mais sourd.
- **Proxy qui redémarre en boucle** — typiquement une alimentation qui faiblit.

Tout ça ressort en entités classiques, plus une notification lisible quand un incident s'ouvre — avec l'action concrète à faire, pas juste un code d'erreur. La notification se retire toute seule quand l'incident se résout.

Concrètement vous obtenez :

- par proxy : `sensor.<proxy>_slots_used` et `_slots_free`, avec la liste des adresses qui occupent les places
- par proxy : `binary_sensor.<proxy>_online` et `sensor.<proxy>_last_device_seen`
- un `binary_sensor.bluesight_incident` global, qui porte le détail de tous les incidents ouverts
- un dump de diagnostic complet à joindre à un rapport de bug
- une carte Lovelace optionnelle, qui dessine les places en pastilles et affiche le fil d'incidents

**BlueSight ne touche à rien.** Il ne libère pas une place, ne casse pas un appairage, ne reflashe rien. Il observe et il vous dit quoi faire. 🔍

## Ce que ça a trouvé chez moi

Le jour où j'ai fini la version 0.3.0, l'intégration a sorti en quelques minutes, sur ma propre installation : un slot fantôme sur un thermostat Daikin BRC1H, et deux tempêtes d'appairage sur deux de mes quatre thermostats. Les entités étaient en `indisponible` depuis un moment et je n'avais aucune idée de pourquoi.

Détail amusant : le slot fantôme a changé de proxy entre deux relevés. L'appareil rebondissait d'un proxy à l'autre en étant retenté en boucle — c'est la signature typique d'une tempête, et c'est exactement le genre de chose qu'on ne peut pas voir sans cet outil.

## Ce qu'il faut

- **Home Assistant ≥ 2025.7**
- **Un ou plusieurs proxys Bluetooth ESPHome** (ou des adaptateurs locaux). Avec un seul, vous avez déjà la visibilité des places et la détection des fantômes et des tempêtes ; la détection de blocage n'a de sens qu'à partir de deux proxys.

Aucune configuration : l'intégration lit ce que HA sait déjà. Rien à toucher côté ESPHome, aucun reflash.

## Installation

Via HACS, en ajoutant le dépôt comme dépôt personnalisé de type `Integration` :

`https://github.com/dasimon135/ha-bluesight`

Puis redémarrage, et Paramètres → Appareils et services → Ajouter une intégration → BlueSight.

⚠️ **La carte Lovelace n'est PAS installée par HACS.** HACS ne s'occupe des fichiers de carte que pour les dépôts de catégorie *Lovelace*, et BlueSight est une *intégration* — vous récupérez donc l'intégration et rien d'autre. Il y a deux étapes manuelles (copier le fichier dans `config/www/`, déclarer la ressource), détaillées dans [`docs/card.md`](https://github.com/dasimon135/ha-bluesight/blob/main/docs/card.md). Je le signale parce que l'échec est silencieux : rien ne plante, la carte n'apparaît simplement jamais. Je me suis fait avoir moi-même. 🙃

Si vous ne voulez pas de JavaScript custom, la même doc donne un équivalent en cartes natives à copier-coller.

## Ce que ça ne fait pas

Autant être honnête sur les limites :

- **La détection de tempête reste une estimation.** Home Assistant n'expose aucun compteur d'échec d'appairage brut, donc je déduis un échec de la seule chose observable : une place rendue alors que l'appareil est indisponible. Un cycle de mesure normal rend aussi sa place, mais laisse ses entités disponibles, donc il n'est pas compté. C'est un bon signal d'alerte, pas un décompte exact.
- **Les slots fantômes ne sont détectés que pour les appareils gérés par Home Assistant.** Si une place est prise par un appareil Bluetooth que HA ne suit pas, je ne peux pas juger s'il est vivant, donc je ne le signale pas plutôt que de crier au loup.
- **C'est en lecture seule**, volontairement. La remédiation guidée, c'est pour plus tard.

## La suite

L'idée d'après, c'est un composant ESPHome optionnel à poser sur les proxys, pour remonter ce que l'API de HA ne donne pas : les vrais compteurs d'échec d'appairage, la RAM Bluetooth, l'état des appairages. Ça remplacerait l'estimation de tempête par une vraie mesure. Puis, à terme, la libération assistée d'une place.

## Retours bienvenus

Le dépôt est ici : `https://github.com/dasimon135/ha-bluesight`

Je suis surtout preneur de retours sur :

- ce que ça détecte chez vous, y compris et surtout les **faux positifs** — c'est le risque principal de ce genre d'outil
- les configurations à beaucoup de proxys, que je n'ai pas sous la main
- les appareils Bluetooth qui bloquent des places chez vous : je soupçonne que ce n'est pas qu'une histoire de Daikin

Si vous avez des volets, thermostats ou capteurs Bluetooth qui passent en `indisponible` sans explication, ça vaut le coup de regarder. Merci d'avance 🙌
