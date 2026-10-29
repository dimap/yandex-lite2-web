package io.github.dimap.yandexstation;

import android.Manifest;
import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.View;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** Окно приложения: WebView с панелью на 127.0.0.1. Пока сервер не ответил — заставка, сервер упал — запускаем снова. */
public class MainActivity extends Activity {
    static final String HOME = "http://127.0.0.1:" + PanelService.PORT + "/";
    static final int PICK_FILE = 1;

    private WebView web;
    private android.widget.FrameLayout root;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private ValueCallback<Uri[]> pending;
    private boolean panelShown;
    private int misses;
    private boolean resumed;
    private volatile float insetTop, insetBottom;   // системные панели в CSS-пикселях — странице, см. pushInsets
    private volatile boolean darkPage = true;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        web.setBackgroundColor(getColor(R.color.bg));
        // WebView — в контейнере: свой отступ WebView не учитывает, а контейнер, сжатый над клавиатурой, сжимает и её
        root = new android.widget.FrameLayout(this);
        root.addView(web, new android.widget.FrameLayout.LayoutParams(-1, -1));
        setContentView(root);
        edgeToEdge();

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setSupportMultipleWindows(false);   // target="_blank" — в shouldOverrideUrlLoading, внешние ссылки — в браузер
        s.setUserAgentString(s.getUserAgentString() + " YandexStationPanel/" + BuildConfigVersion.name(this));
        CookieManager.getInstance().setAcceptCookie(true);
        web.addJavascriptInterface(new Bridge(), "AndroidPanel");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
                Uri u = req.getUrl();
                if ("127.0.0.1".equals(u.getHost()) || "localhost".equals(u.getHost())) return false;
                openOutside(u);
                return true;
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                pushInsets();
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                if (req.isForMainFrame()) showSplash();
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> cb, FileChooserParams params) {
                if (pending != null) pending.onReceiveValue(null);
                pending = cb;
                Intent i = params.createIntent();
                if (params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE) i.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
                try {
                    startActivityForResult(i, PICK_FILE);
                } catch (ActivityNotFoundException e) {
                    pending = null;
                    return false;
                }
                return true;
            }
        });
        web.setDownloadListener((url, ua, disposition, mime, length) ->
                new Thread(() -> download(url, URLUtil.guessFileName(url, disposition, mime), mime)).start());

        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 0);
        }
        startPanel();
        showSplash();
    }

    @Override
    protected void onResume() {
        super.onResume();
        resumed = true;
        ui.post(watch);
    }

    @Override
    protected void onPause() {
        resumed = false;
        ui.removeCallbacks(watch);
        super.onPause();
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else moveTaskToBack(true);
    }

    @Override
    protected void onActivityResult(int req, int res, Intent data) {
        if (req != PICK_FILE || pending == null) return;
        Uri[] uris = null;
        if (res == RESULT_OK && data != null) {
            if (data.getClipData() != null) {
                uris = new Uri[data.getClipData().getItemCount()];
                for (int i = 0; i < uris.length; i++) uris[i] = data.getClipData().getItemAt(i).getUri();
            } else if (data.getData() != null) {
                uris = new Uri[]{data.getData()};
            }
        }
        pending.onReceiveValue(uris);
        pending = null;
    }

    /** Окно под статус-баром и панелью навигации, как у обычного приложения; отступы страница берёт из --sa-top/--sa-bottom. */
    private void edgeToEdge() {
        Window w = getWindow();
        w.setStatusBarColor(Color.TRANSPARENT);
        w.setNavigationBarColor(Color.TRANSPARENT);
        if (Build.VERSION.SDK_INT >= 29) {
            w.setStatusBarContrastEnforced(false);
            w.setNavigationBarContrastEnforced(false);
        }
        if (Build.VERSION.SDK_INT >= 28) {
            w.getAttributes().layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
        }
        if (Build.VERSION.SDK_INT >= 30) w.setDecorFitsSystemWindows(false);
        barsAppearance();
        float d = getResources().getDisplayMetrics().density;
        root.setOnApplyWindowInsetsListener((v, in) -> {
            int top, bottom, ime;
            if (Build.VERSION.SDK_INT >= 30) {
                Insets bars = in.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                top = bars.top;
                bottom = bars.bottom;
                ime = in.getInsets(WindowInsets.Type.ime()).bottom;
            } else {   // до Android 11 клавиатура входит в нижний отступ
                top = in.getSystemWindowInsetTop();
                bottom = in.getSystemWindowInsetBottom();
                ime = bottom > 120 * d ? bottom : 0;
            }
            // клавиатура открыта — сжимаем WebView над ней, нижний отступ странице уже не нужен
            if (ime != v.getPaddingBottom()) android.util.Log.d(PanelService.TAG, "клавиатура: " + ime + " px");
            v.setPadding(0, 0, 0, ime);
            insetTop = top / d;
            insetBottom = ime > 0 ? 0 : bottom / d;
            pushInsets();
            // отступы страница берёт от нас — WebView их больше не нужны
            return Build.VERSION.SDK_INT >= 30 ? WindowInsets.CONSUMED : in.consumeSystemWindowInsets();
        });
    }

    private void pushInsets() {
        web.evaluateJavascript("(s=>{s.setProperty('--sa-top','" + insetTop + "px');s.setProperty('--sa-bottom','"
                + insetBottom + "px')})(document.documentElement.style)", null);
    }

    /** Значки статус-бара и навигации — тёмные на светлой теме страницы, светлые на тёмной. */
    @SuppressWarnings("deprecation")
    private void barsAppearance() {
        Window w = getWindow();
        if (Build.VERSION.SDK_INT >= 30) {
            int light = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
            w.getInsetsController().setSystemBarsAppearance(darkPage ? 0 : light, light);
        } else {
            int f = View.SYSTEM_UI_FLAG_LAYOUT_STABLE | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION;
            if (!darkPage && Build.VERSION.SDK_INT >= 23) f |= View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR;
            if (!darkPage && Build.VERSION.SDK_INT >= 26) f |= View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            w.getDecorView().setSystemUiVisibility(f);
        }
    }

    private void startPanel() {
        Intent i = new Intent(this, PanelService.class);
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i);
        else startService(i);
    }

    private void showSplash() {
        panelShown = false;
        web.loadDataWithBaseURL(null, "<!doctype html><meta name=viewport content='width=device-width'>"
                + "<style>body{margin:0;height:100vh;display:grid;place-items:center;background:#0F1115;color:#9aa0aa;"
                + "font:15px system-ui}i{display:block;width:28px;height:28px;margin:0 auto 14px;border:3px solid #333;"
                + "border-top-color:#8B5CF6;border-radius:50%;animation:r 1s linear infinite}@keyframes r{to{transform:rotate(1turn)}}</style>"
                + "<div><i></i>" + getString(R.string.starting) + "</div>", "text/html", "utf-8", null);
    }

    /** Раз в 2 секунды, пока окно на экране: жив ли сервер. Не отвечает — запускаем службу; ответил — показываем панель. */
    private final Runnable watch = new Runnable() {
        @Override
        public void run() {
            new Thread(() -> {
                boolean up = probe();
                ui.post(() -> {
                    if (!resumed) return;
                    if (up) {
                        misses = 0;
                        if (!panelShown) {
                            panelShown = true;
                            web.loadUrl(HOME);
                        }
                    } else if (++misses % 3 == 1) {
                        startPanel();
                    }
                    ui.postDelayed(watch, up ? 4000 : 700);
                });
            }).start();
        }
    };

    private static boolean probe() {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(HOME + "manifest.webmanifest").openConnection();
            c.setConnectTimeout(800);
            c.setReadTimeout(3000);
            int code = c.getResponseCode();
            c.disconnect();
            return code > 0;
        } catch (IOException e) {
            return false;
        }
    }

    private void openOutside(Uri u) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, u));
        } catch (ActivityNotFoundException ignored) {
        }
    }

    /** Ссылки на скачивание с сервера панели (файлы колонки и т. п.): сами качаем и кладём в «Загрузки». */
    private void download(String url, String name, String mime) {
        if (!url.startsWith(HOME)) {
            ui.post(() -> openOutside(Uri.parse(url)));
            return;
        }
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null) c.setRequestProperty("Cookie", cookie);
            c.setReadTimeout(120000);
            if (c.getResponseCode() != 200) throw new IOException("HTTP " + c.getResponseCode());
            String cd = c.getHeaderField("Content-Disposition");
            if (cd != null) name = URLUtil.guessFileName(url, cd, mime);
            try (InputStream in = c.getInputStream()) {
                save(name, c.getContentType() != null ? c.getContentType() : mime, in);
            }
        } catch (IOException e) {
            ui.post(() -> Toast.makeText(this, R.string.save_failed, Toast.LENGTH_LONG).show());
        }
    }

    private void save(String name, String mime, InputStream in) throws IOException {
        OutputStream out;
        if (Build.VERSION.SDK_INT >= 29) {
            ContentValues v = new ContentValues();
            v.put(MediaStore.Downloads.DISPLAY_NAME, name);
            v.put(MediaStore.Downloads.MIME_TYPE, mime);
            Uri uri = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
            if (uri == null) throw new IOException("MediaStore");
            out = getContentResolver().openOutputStream(uri);
            if (out == null) throw new IOException("MediaStore");
        } else {
            File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
            dir.mkdirs();
            out = new FileOutputStream(new File(dir, name));
        }
        try (OutputStream o = out) {
            byte[] buf = new byte[65536];
            for (int n; (n = in.read(buf)) > 0; ) o.write(buf, 0, n);
        }
        final String shown = name;
        ui.post(() -> Toast.makeText(this, getString(R.string.saved, shown), Toast.LENGTH_LONG).show());
    }

    /** window.AndroidPanel — для файлов, которые страница собирает сама (blob: WebView не скачивает). */
    class Bridge {
        /** Отступы под системные панели — ещё до первой отрисовки, «верх,низ» в CSS-пикселях. */
        @JavascriptInterface
        public String insets() {
            return insetTop + "," + insetBottom;
        }

        @JavascriptInterface
        public void theme(boolean dark) {
            if (dark == darkPage) return;
            darkPage = dark;
            ui.post(MainActivity.this::barsAppearance);
        }

        @JavascriptInterface
        public boolean save(String name, String mime, String base64) {
            try {
                MainActivity.this.save(new File(name).getName(), mime, new ByteArrayInputStream(Base64.decode(base64, Base64.DEFAULT)));
                return true;
            } catch (IOException | IllegalArgumentException e) {
                return false;
            }
        }
    }

    static class BuildConfigVersion {
        static String name(Activity a) {
            try {
                return a.getPackageManager().getPackageInfo(a.getPackageName(), 0).versionName;
            } catch (PackageManager.NameNotFoundException e) {
                return "?";
            }
        }
    }
}
