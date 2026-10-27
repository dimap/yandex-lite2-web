# 3D sources and licenses

- Lite 2: `lite2.stl` is the original, unchanged STL «Yandex Station Lite 2 | Яндекс станция лайт 2»
  by **vanyabeat**, https://www.thingiverse.com/thing:7349611 , licensed under
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The panel only loads and colours it in the
  browser (`model-lite2.js`). The MIT license of the panel code does not apply to this file. The STEP source
  is available on the Thingiverse page.
- Station 2, Mini 3 Pro, Mini 3 and Station Lite (1st gen): original approximate visual reconstructions in
  model-studio.js (MIT), based on the manufacturer's public product photographs.
  They are not official CAD, downloaded commercial meshes, or manufacturing drawings.
- Station Mini 2 with clock (YNDX-00020): original approximate reconstruction in model-studio.js (MIT) from an
  owner's photographs and caliper measurements of a white unit, checked against the official 97 × 54 mm,
  260 g specification (https://alice.yandex.ru/support/ru/station/meet/characteristics-mini2). The display
  is drawn in the panel's shared clock geometry (clock-geometry.js): 4 × 7 segments, colon, hyphen and
  degree sign — 32 white LEDs, no decimal points. The photographs are not bundled.

References:
- https://alice.yandex.ru/station-gen2
- https://alice.yandex.ru/station-mini-gen3-pro
- https://alice.yandex.ru/station-mini-gen3
- https://alice.yandex.ru/station-lite
- https://alice.yandex.ru/support/ru/station/meet/characteristics-mini2

Mini 2 is a 97 × 54 mm fabric cylinder: slotted plastic lid with a lit Alice ring in the centre, «−»/«+» marks,
four microphone holes, a plastic back strip with the microphone-off button, AUX and a round power jack,
and a small «Я» tab near the top edge. Mini 3 has a lower 102 × 60.5 mm body and central indicator; Mini 3 Pro has a
102 × 71 mm body and perimeter light strip. Station Lite is an 88 mm puck on a ribbed
black base; the edge slots are decorative, only the Alice logo in the centre lights up (no clock). Details and colours are approximate.
Product photographs are not bundled or used as textures. All models use lightweight
materials with no shadows, ground plane, bump mapping or physical materials. Lite 2
keeps the original main geometry, materials and lighting; colour, auto-rotation and view reset live in
«Настроить» on the «Обзор» page. Three.js and all required assets are local.

The preview page uses explicit demo clock/LED data. This is not a hardware test.
Mini 2's central RGB diffuser shows the approximate mean colour of its six native zones;
individual LED optics are not reconstructed.
