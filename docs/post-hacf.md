# [Intégration] BlueSight — voir ce qui bloque vraiment vos proxys Bluetooth

> **Corps du fil en ligne, à jour pour la v0.6.5.**
>
> Le fil est déjà publié et ceci en est le premier message :
> https://forum.hacf.fr/t/integration-bluesight-voir-ce-qui-bloque-vraiment-vos-proxys-bluetooth/83183
> Mettez-le à jour en **éditant ce message**, pas en ouvrant un nouveau fil : un
> lecteur arrive sur le premier message, donc une correction postée en réponse
> est une correction que presque personne ne lit.
>
> Capture à joindre : `images/card_fr.png`. Discourse veut un envoi de
> fichier, pas un lien — glissez-le dans l'éditeur.
>
> La version anglaise est dans `post-community.md`.

Bonjour à tous 👋

Je partage une intégration custom que j'ai écrite pour un problème qui m'a fait perdre pas mal de temps, et sur lequel je n'ai trouvé aucun outil.

Elle s'appelle `bluesight`.

## Le problème

Le Bluetooth de Home Assistant a deux étages, et on n'en voit qu'un.

Le premier, c'est **ce que chaque proxy entend** : quels appareils passent à portée. Celui-là est bien couvert depuis HA 2025.2 par le *Moniteur d'annonces* (Paramètres → Appareils → Bluetooth → Configurer). On y voit très bien quel proxy capte quel appareil.

Le second, c'est **le nombre de connexions qu'un proxy peut tenir en même temps**. Un ESP32 en tient trois par défaut. Ces quelques places — les « slots » — sont une ressource partagée, et elles sont totalement invisibles. Aucun écran dans Home Assistant ne vous dit qui les occupe.

Et c'est là que ça casse. Quand une place reste bloquée par un appareil qui ne répond plus, elle n'est jamais rendue. Vos autres appareils Bluetooth passent en `indisponible`, sans erreur, sans message, sans rien. Vous redémarrez le proxy au hasard, ça remarche un moment, ça recommence. 😐

## Ce que fait BlueSight

Il lit les allocations de slots que Home Assistant tient déjà en interne, et il en tire sept diagnostics.

**Côté appareils :**

- **Blocage (« deadlock »)** — la même adresse Bluetooth est réservée sur **deux proxys ou plus** en même temps. C'est impossible physiquement : un appareil Bluetooth ne parle qu'à un seul interlocuteur. Donc les places en trop sont des reliquats qui ne seront jamais rendus. C'est un vrai bug documenté du cœur de HA ([issue #176516](https://github.com/home-assistant/core/issues/176516)), et la seule méthode proposée dans le fil, c'est d'activer les logs debug et de les lire à la main.
- **Slot fantôme** — un proxy réserve toujours une place pour un appareil dont **toutes** les entités sont passées en `indisponible`. La place est dépensée pour rien.
- **Tempête d'appairage** — un appareil enchaîne les échecs de connexion en rafale, ce qui secoue le proxy et monopolise les places.
- **Clé d'appairage manquante** — un appareil se fait refuser encore et encore par un proxy qui n'a pas sa clé. Nécessite le firmware optionnel plus bas : Home Assistant ne voit ni l'un ni l'autre.

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
- une carte Lovelace qui dessine les places en pastilles, **nomme l'appareil qui occupe chacune**, et affiche le fil d'incidents

**BlueSight ne touche à rien.** Il ne libère pas une place, ne casse pas un appairage, ne reflashe rien. Il observe et il vous dit quoi faire. 🔍

## Optionnel : de la mesure au lieu d'une estimation

Deux des diagnostics ci-dessus sont des déductions quand on n'a que l'API de Home Assistant, parce que HA ne voit **pas du tout** les échecs d'appairage (SMP) et ne sait pas lire le magasin de clés d'un proxy.

D'où un **composant ESPHome optionnel**, à poser sur des proxys que vous avez déjà. Deux blocs à coller dans une config existante, sans rien changer à la radio ni au nombre de connexions — c'est un observateur passif du flux d'événements BLE, et il ne consomme aucune place :

```yaml
external_components:
  - source: github://dasimon135/ha-bluesight@v0.6.5
    components: [bluesight]

bluesight:
```

Il publie trois choses que Home Assistant ne peut pas voir : les **compteurs d'échec SMP**, le **magasin de clés (NVS)** du proxy, et le **temps d'inactivité par connexion**. La détection de tempête passe d'une estimation à un décompte, le diagnostic « clé d'appairage manquante » apparaît avec un remède exact — *réappairez en passant par ce proxy précis*, parce que les clés sont propres à chaque proxy et qu'appairer via celui que HA choisira ensuite ne corrigera rien — et un slot fantôme devient jugeable même pour un appareil que Home Assistant ne gère pas du tout.

Le point important pour une flotte hétérogène : la preuve est remplacée **proxy par proxy**. Un proxy qui le fait tourner est jugé sur des mesures, un autre garde l'estimation, et l'intégration fonctionne très bien sans aucun proxy équipé. Vous pouvez en flasher un seul et voir la différence avant de décider.

## Ce que ça a trouvé chez moi

Le jour où j'ai fini la version 0.3.0, l'intégration a sorti en quelques minutes, sur ma propre installation : un slot fantôme sur un thermostat Daikin BRC1H, et deux tempêtes d'appairage sur deux de mes quatre thermostats. Les entités étaient en `indisponible` depuis un moment et je n'avais aucune idée de pourquoi.

Détail amusant : le slot fantôme a changé de proxy entre deux relevés. L'appareil rebondissait d'un proxy à l'autre en étant retenté en boucle — c'est la signature typique d'une tempête, et c'est exactement le genre de chose qu'on ne peut pas voir sans cet outil.

Plus récemment, il a trouvé un thermostat injoignable depuis des heures parce qu'un proxy recevait sans cesse des connexions pour un appareil dont il n'avait pas la clé, pendant que le proxy qui **avait** la clé restait totalement inoccupé. Celui-là, à la main, on ne le trouve pas.

## Il s'est aussi trompé, et c'est là-dessus que j'ai besoin de vous

Deux fois en une semaine, chez moi, BlueSight a signalé une panne sur quelque chose de parfaitement sain.

Un thermostat connecté et fonctionnel via le proxy qui détient sa clé a été signalé comme « à réappairer », parce qu'un **autre** proxy l'avait refusé à un moment dans le passé — le compte était le compteur de vie du firmware, donc l'incident ne pouvait jamais s'éteindre. La 0.6.4 en a fait un décompte d'échecs **dans une fenêtre glissante** : une panne qui cesse d'être vraie cesse d'être signalée.

L'autre était un lien proxy BLE Mesh : en pleine santé, mais silencieux neuf heures parce que rien n'avait bougé sur le mesh, et invisible au registre d'appareils de Home Assistant, donc rien ne pouvait corroborer ce silence. Signalé comme slot bloqué.

Les deux sont la même classe d'erreur, et c'est le risque qui compte pour ce genre d'outil : **une alerte sur quelque chose qui va bien vous apprend à ignorer l'outil**, et ça ne se rattrape pas. Je préfère largement un retour sur un faux positif que dix confirmations.

## Ce qu'il faut

- **Home Assistant ≥ 2025.7**
- **Un ou plusieurs proxys Bluetooth ESPHome** (ou des adaptateurs locaux). Avec un seul, vous avez déjà la visibilité des places et la détection des fantômes et des tempêtes ; la détection de blocage n'a de sens qu'à partir de deux proxys.

Aucune configuration : l'intégration lit ce que HA sait déjà. Rien à toucher côté ESPHome, sauf si vous voulez le composant optionnel ci-dessus.

L'interface et les notifications suivent votre langue ; l'anglais et le français sont livrés. Le backend rend dans la langue de l'installation, la carte dans la langue du profil de chaque utilisateur.

## Installation

Via HACS, en ajoutant le dépôt comme dépôt personnalisé de type `Integration` :

`https://github.com/dasimon135/ha-bluesight`

Puis redémarrage, et Paramètres → Appareils et services → Ajouter une intégration → BlueSight.

La carte est livrée **avec** l'intégration, qui déclare elle-même sa ressource de tableau de bord : rien à copier, rien à enregistrer à la main. (C'était deux étapes manuelles jusqu'à la 0.4.0, et les deux échouaient en silence — rien ne plantait, la carte n'apparaissait simplement jamais. Si vous ne voulez pas de JavaScript custom du tout, [`docs/card.md`](https://github.com/dasimon135/ha-bluesight/blob/main/docs/card.md) donne un équivalent en cartes natives à copier-coller.)

## Ce que ça ne fait pas

Autant être honnête sur les limites :

- **La détection de tempête reste une estimation sur tout proxy qui ne la mesure pas.** Avec les seules données de HA, aucun compteur SMP brut n'existe, donc je déduis un échec de la seule chose observable : une place rendue alors que l'appareil est indisponible. Un cycle de mesure normal rend aussi sa place, mais laisse ses entités disponibles, donc il n'est pas compté. Bon signal d'alerte, pas un décompte exact. Le composant optionnel le remplace par un vrai comptage — proxy par proxy, donc sur une flotte mixte l'estimation reste active sur chaque nœud non équipé.
- **Les slots fantômes sont jugés sur l'état des entités pour les appareils que HA connaît, et seulement là où un proxy le mesure pour les autres.** Une place prise par un périphérique que HA ne suit pas est considérée comme vivante plutôt que signalée — volontairement, parce que l'autre signal possible (la présence d'annonces) déclencherait un faux positif sur toute connexion permanente saine. Là où un proxy fait tourner le composant, le silence devient mesurable — mais un silence mesuré n'est pas une preuve non plus : un lien légitimement calme ressemble exactement à un lien bloqué. C'est pour ça que le seuil d'inactivité est un réglage avec un plancher, et pas une constante.
- **C'est en lecture seule**, volontairement. La remédiation guidée, c'est pour plus tard — voir ci-dessous.

## La suite

Le composant ESPHome optionnel était l'étape d'après annoncée, et il est sorti en 0.6.0. Deux éléments de son esquisse ne sont pas passés : les compteurs de refus de connexion, toujours possibles, et la RAM Bluetooth, qui relève de la santé du nœud plutôt que de la couche connexion et qu'ESPHome expose déjà tout seul.

Ensuite vient la remédiation guidée — « libérer cette place », réappairage assisté. Je ne la commence **volontairement pas** encore. Elle agit sur votre pile Bluetooth en se fiant à un verdict, et les deux faux positifs racontés plus haut sont arrivés en une seule semaine sur la seule flotte que je peux tester. Ce socle doit être juste sur les installations des autres avant qu'on laisse quoi que ce soit agir dessus.

## Retours bienvenus

Le dépôt est ici : `https://github.com/dasimon135/ha-bluesight`

Je suis surtout preneur de retours sur :

- les **faux positifs** — le risque principal de ce genre d'outil, et ce que je ne peux pas trouver seul. Surtout sur des appareils BLE que Home Assistant ne gère pas, où la seule preuve disponible est depuis combien de temps une connexion est silencieuse.
- les **configurations à beaucoup de proxys**, que je n'ai pas sous la main. Tout ici est réglé contre quatre proxys dans une seule maison.
- les appareils Bluetooth qui bloquent des places chez vous : je soupçonne que ce n'est pas qu'une histoire de Daikin.

Si vous avez des volets, thermostats ou capteurs Bluetooth qui passent en `indisponible` sans explication, ça vaut le coup de regarder. Merci d'avance 🙌
