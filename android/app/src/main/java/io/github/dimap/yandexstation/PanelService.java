package io.github.dimap.yandexstation;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.content.pm.ServiceInfo;
import android.content.res.AssetManager;
import android.net.wifi.WifiManager;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import org.json.JSONArray;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;

/**
 * Сервер панели (server.py) в отдельном процессе «:panel». Python — Chaquopy, adb — pyadb.
 * Перезапуск сервера (смена колонки или профиля) — это перезапуск процесса: сохраняем аргументы и
 * завершаемся. Службу снова запускает MainActivity (страница потеряла связь) или сама система (START_STICKY).
 */
public class PanelService extends Service {
    static final String TAG = "panel";
    static final int PORT = 8765;
    static final String CHANNEL = "panel";
    static final String ACTION_STOP = "stop";
    static final String PREF_ARGS = "args";

    private PowerManager.WakeLock wake;
    private WifiManager.MulticastLock multicast;
    private static Thread server;

    @Override
    public void onCreate() {
        super.onCreate();
        startForeground();
        // колонка в той же сети: без блокировок Wi-Fi засыпает, и связь adb рвётся
        wake = ((PowerManager) getSystemService(POWER_SERVICE)).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "panel:server");
        wake.acquire();
        multicast = ((WifiManager) getApplicationContext().getSystemService(WIFI_SERVICE)).createMulticastLock("panel");
        multicast.acquire();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopForeground(true);
            stopSelf();
            android.os.Process.killProcess(android.os.Process.myPid());
            return START_NOT_STICKY;
        }
        startServer();
        return START_STICKY;
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        if (wake.isHeld()) wake.release();
        if (multicast.isHeld()) multicast.release();
        super.onDestroy();
    }

    private synchronized void startServer() {
        if (server != null) return;
        server = new Thread(() -> {
            try {
                File panel = extractPanel();
                String args = prefs(this).getString(PREF_ARGS, null);
                if (args == null) args = defaultArgs(this).toString();
                Python py = Python.getInstance();
                PyObject main = py.getModule("panel_android");
                main.callAttr("main", panel.getAbsolutePath(), args, this);
            } catch (Throwable e) {
                Log.e(TAG, "сервер панели упал", e);
            }
            // server.main() вернулся или упал — процесс без сервера не нужен, интерфейс запустит заново
            android.os.Process.killProcess(android.os.Process.myPid());
        }, "panel-server");
        server.start();
    }

    /** Вызывается из Python вместо os.execv: аргументы — без имени скрипта. */
    @SuppressWarnings("unused")
    public void restart(String argsJson) {
        try {
            JSONArray args = new JSONArray(argsJson);
            prefs(this).edit().putString(PREF_ARGS, args.toString()).commit();
        } catch (Exception e) {
            Log.e(TAG, "аргументы перезапуска", e);
        }
        Log.i(TAG, "перезапуск сервера: " + argsJson);
        android.os.Process.killProcess(android.os.Process.myPid());
    }

    static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("panel", MODE_PRIVATE);
    }

    static JSONArray defaultArgs(Context c) {
        File data = new File(c.getFilesDir(), "data");
        data.mkdirs();
        JSONArray a = new JSONArray();
        for (String s : new String[]{"--host", "127.0.0.1", "--port", String.valueOf(PORT), "--https-port", "0",
                "--data-dir", data.getAbsolutePath()}) a.put(s);
        return a;
    }

    /** Файлы панели из assets/panel → files/panel; заново — после каждой установки APK. */
    private File extractPanel() throws Exception {
        File dir = new File(getFilesDir(), "panel");
        PackageInfo pi = getPackageManager().getPackageInfo(getPackageName(), 0);
        String stamp = pi.versionName + "@" + pi.lastUpdateTime;
        File mark = new File(dir, ".extracted");
        if (mark.exists() && stamp.equals(readText(mark))) return dir;
        deleteTree(dir);
        copyAssets(getAssets(), "panel", dir);
        try (OutputStream o = new FileOutputStream(mark)) {
            o.write(stamp.getBytes("UTF-8"));
        }
        return dir;
    }

    private static void copyAssets(AssetManager am, String path, File to) throws IOException {
        String[] list = am.list(path);
        if (list == null || list.length == 0) {   // файл (у пустых папок в assets детей тоже нет — их там не бывает)
            to.getParentFile().mkdirs();
            try (InputStream in = am.open(path); OutputStream out = new FileOutputStream(to)) {
                byte[] buf = new byte[65536];
                for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
            }
            return;
        }
        to.mkdirs();
        for (String name : list) copyAssets(am, path + "/" + name, new File(to, name));
    }

    private static void deleteTree(File f) {
        File[] kids = f.listFiles();
        if (kids != null) for (File k : kids) deleteTree(k);
        f.delete();
    }

    private static String readText(File f) {
        try (InputStream in = new java.io.FileInputStream(f)) {
            byte[] b = new byte[(int) f.length()];
            int n = in.read(b);
            return new String(b, 0, Math.max(n, 0), "UTF-8");
        } catch (IOException e) {
            return "";
        }
    }

    private void startForeground() {
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, getString(R.string.channel),
                    NotificationManager.IMPORTANCE_MIN));
        }
        int imm = Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0;
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), imm);
        PendingIntent stop = PendingIntent.getService(this, 1,
                new Intent(this, PanelService.class).setAction(ACTION_STOP), imm);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26 ? new Notification.Builder(this, CHANNEL) : new Notification.Builder(this);
        Notification n = b.setSmallIcon(android.R.drawable.stat_sys_data_bluetooth)
                .setContentTitle(getString(R.string.app_name))
                .setContentText(getString(R.string.running))
                .setContentIntent(open)
                .setOngoing(true)
                .addAction(new Notification.Action.Builder(null, getString(R.string.stop), stop).build())
                .build();
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        } else {
            startForeground(1, n);
        }
    }
}
