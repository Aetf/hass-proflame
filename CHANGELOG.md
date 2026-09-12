# Changelog

## [0.3.0](https://github.com/Aetf/hass-proflame/compare/v0.2.0...v0.3.0) (2026-09-12)


### Features

* choose the receiver independently of the transmitter ([59d1afb](https://github.com/Aetf/hass-proflame/commit/59d1afb6d26d11c6f739034b35bd84c0238b3fc8))
* hear the handset through an ESPHome receiver ([16f8ec1](https://github.com/Aetf/hass-proflame/commit/16f8ec1e9f0f7e3db25718433f5aaf5ae0033801))
* list and preselect only receivers confirmed to hear ([75c8cc3](https://github.com/Aetf/hass-proflame/commit/75c8cc36282b6f48e404de18570ac674ce54eacc))


### Bug Fixes

* carry the inter-frame gap in the command timings ([da0a1dc](https://github.com/Aetf/hass-proflame/commit/da0a1dc892b5a7afd7d31a9ccd760eba4619411d))
* require proflame 0.1.1, which decodes frames ending on a space ([3ae9a73](https://github.com/Aetf/hass-proflame/commit/3ae9a73c4a65834567dd5695b94d56ae1c95714b))

## [0.2.0](https://github.com/Aetf/hass-proflame/compare/v0.1.0...v0.2.0) (2026-08-20)


### Features

* ship the Proflame brand images, and drop the HACS brands workaround ([72c1c37](https://github.com/Aetf/hass-proflame/commit/72c1c37e321cc4602d2bbaaa1e9a221a4d20c5e7))

## 0.1.0 (2026-08-20)


### Features

* depend on the published proflame protocol library ([fd3977c](https://github.com/Aetf/hass-proflame/commit/fd3977cc6d5ba90937ece55192947e62b0ceb445))


### Bug Fixes

* drop the null switch name from the translations, ignore brands for now ([6c0dec7](https://github.com/Aetf/hass-proflame/commit/6c0dec704350e2af09142ba5a3e8e8239657db97))
