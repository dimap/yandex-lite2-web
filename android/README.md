# Приложение для Android

Панель целиком внутри APK: Python (Chaquopy) запускает `server.py` на `127.0.0.1:8765`, окно приложения — WebView.
Отдельный adb не нужен — используется `pyadb.py` (adb по сети на чистом Python), поэтому колонка подключается только по Wi‑Fi.

Сервер работает в фоновой службе (уведомление «Панель колонки работает в фоне», кнопка «Остановить»).
Обновляется приложение установкой нового APK поверх старого — вход в Яндекс, настройки и история сохраняются.

## Сборка

Нужны JDK 17 или 21 и Android SDK (platform 34). Gradle 8.14:

```sh
cd android
export JAVA_HOME=/usr/lib/jvm/java-21-openjdk
echo "sdk.dir=$HOME/Android/Sdk" > local.properties
gradle assembleRelease          # → app/build/outputs/apk/release/app-release.apk
```

Без `keystore.properties` релиз подписывается отладочным ключом. Свой ключ:

```properties
storeFile=release.jks
storePassword=…
keyAlias=panel
keyPassword=…
```
