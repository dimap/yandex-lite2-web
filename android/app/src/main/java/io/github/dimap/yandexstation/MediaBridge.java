package io.github.dimap.yandexstation;

import android.app.Notification;
import android.app.PendingIntent;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.media.MediaMetadata;
import android.media.VolumeProvider;
import android.media.session.MediaSession;
import android.media.session.PlaybackState;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Objects;

/**
 * Колонка как плеер телефона: что играет — в шторке и на экране блокировки, кнопки управления,
 * а кнопки громкости телефона меняют громкость колонки (удалённое воспроизведение, как у Chromecast).
 * Состояние берём у своего же сервера (/api/music), команды шлём туда же.
 */
class MediaBridge {
    static final String ACTION_PREFIX = "media:";
    private static final int VOL_STEPS = 10;

    private final PanelService service;
    private final MediaSession session;
    private final Handler bg;
    private final VolumeProvider volume = new VolumeProvider(VolumeProvider.VOLUME_CONTROL_ABSOLUTE, VOL_STEPS, 0) {
        @Override
        public void onSetVolumeTo(int v) {
            setVolume(v);
        }

        @Override
        public void onAdjustVolume(int dir) {
            setVolume(getCurrentVolume() + dir);
        }
    };

    private String shownKey;        // что сейчас в уведомлении: не перерисовываем без изменений
    private String coverUrl;
    private Bitmap cover;
    private boolean active;
    private boolean stopped;

    MediaBridge(PanelService service) {
        this.service = service;
        HandlerThread t = new HandlerThread("media");
        t.start();
        bg = new Handler(t.getLooper());
        session = new MediaSession(service, "station");
        session.setCallback(new MediaSession.Callback() {
            @Override public void onPlay() { cmd("play", null); }
            @Override public void onPause() { cmd("stop", null); }
            @Override public void onStop() { cmd("stop", null); }
            @Override public void onSkipToNext() { cmd("next", null); }
            @Override public void onSkipToPrevious() { cmd("prev", null); }
            @Override public void onSeekTo(long ms) { cmd("rewind", "\"position\":" + ms / 1000.0); }
        }, bg);
        session.setPlaybackToRemote(volume);
        int imm = Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0;
        session.setSessionActivity(PendingIntent.getActivity(service, 0, new Intent(service, MainActivity.class), imm));
    }

    void start() {
        bg.post(poll);
    }

    void stop() {
        stopped = true;
        bg.removeCallbacksAndMessages(null);
        session.release();
    }

    /** Кнопки уведомления приходят в службу как Intent с действием «media:команда». */
    void onAction(String action) {
        String c = action.substring(ACTION_PREFIX.length());
        bg.post(() -> cmd(c, null));
    }

    private final Runnable poll = new Runnable() {
        @Override
        public void run() {
            if (stopped) return;
            boolean playing = false;
            try {
                JSONObject g = new JSONObject(http("GET", "/api/music", null)).optJSONObject("glagol");
                JSONObject st = g == null ? null : g.optJSONObject("state");
                playing = st != null && st.optBoolean("playing");
                update(st);
            } catch (Exception e) {
                update(null);   // сервер перезапускается или нет связи с колонкой
            }
            bg.postDelayed(this, playing ? 1500 : 3000);
        }
    };

    private void update(JSONObject st) {
        JSONObject p = st == null ? null : st.optJSONObject("playerState");
        String title = p == null ? "" : p.optString("title");
        if (p == null || title.isEmpty()) {
            if (active) {
                active = false;
                shownKey = null;
                session.setActive(false);
                service.showNotification(null);
            }
            return;
        }
        boolean playing = st.optBoolean("playing");
        String artist = p.optString("subtitle");
        long dur = (long) (p.optDouble("duration", 0) * 1000);
        long pos = (long) (p.optDouble("progress", 0) * 1000);
        JSONObject extra = p.optJSONObject("extra");
        String uri = extra == null ? "" : extra.optString("coverURI");
        String url = uri.isEmpty() ? null : "https://" + uri.replace("%%", "400x400");
        if (!Objects.equals(url, coverUrl)) {
            coverUrl = url;
            cover = url == null ? null : loadCover(url);
        }
        volume.setCurrentVolume((int) Math.round(st.optDouble("volume", 0) * VOL_STEPS));

        long actions = PlaybackState.ACTION_PLAY | PlaybackState.ACTION_PAUSE | PlaybackState.ACTION_PLAY_PAUSE
                | PlaybackState.ACTION_STOP;
        if (p.optBoolean("hasNext", true)) actions |= PlaybackState.ACTION_SKIP_TO_NEXT;
        if (p.optBoolean("hasPrev", true)) actions |= PlaybackState.ACTION_SKIP_TO_PREVIOUS;
        if (p.optBoolean("hasProgressBar") && dur > 0) actions |= PlaybackState.ACTION_SEEK_TO;
        session.setPlaybackState(new PlaybackState.Builder()
                .setActions(actions)
                .setState(playing ? PlaybackState.STATE_PLAYING : PlaybackState.STATE_PAUSED, pos, playing ? 1f : 0f,
                        SystemClock.elapsedRealtime())
                .build());

        String key = title + "\n" + artist + "\n" + dur + "\n" + playing + "\n" + coverUrl + "\n" + (cover != null);
        if (key.equals(shownKey)) return;
        shownKey = key;
        MediaMetadata.Builder m = new MediaMetadata.Builder()
                .putString(MediaMetadata.METADATA_KEY_TITLE, title)
                .putString(MediaMetadata.METADATA_KEY_ARTIST, artist)
                .putLong(MediaMetadata.METADATA_KEY_DURATION, dur);
        if (cover != null) m.putBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART, cover);
        session.setMetadata(m.build());
        if (!active) {
            active = true;
            session.setActive(true);
        }
        service.showNotification(notification(title, artist, playing));
    }

    private Notification notification(String title, String artist, boolean playing) {
        Notification.Builder b = service.notificationBuilder()
                .setContentTitle(title)
                .setContentText(artist)
                .setLargeIcon(cover)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .addAction(action(android.R.drawable.ic_media_previous, "Назад", "prev"))
                .addAction(playing ? action(android.R.drawable.ic_media_pause, "Пауза", "stop")
                        : action(android.R.drawable.ic_media_play, "Играть", "play"))
                .addAction(action(android.R.drawable.ic_media_next, "Дальше", "next"))
                .setStyle(new Notification.MediaStyle().setMediaSession(session.getSessionToken()).setShowActionsInCompactView(0, 1, 2));
        return b.build();
    }

    private Notification.Action action(int icon, String label, String cmd) {
        int imm = Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0;
        PendingIntent pi = PendingIntent.getService(service, cmd.hashCode(),
                new Intent(service, PanelService.class).setAction(ACTION_PREFIX + cmd), imm);
        return new Notification.Action.Builder(icon, label, pi).build();
    }

    private void setVolume(int v) {
        int c = Math.max(0, Math.min(VOL_STEPS, v));
        volume.setCurrentVolume(c);
        bg.post(() -> cmd("setVolume", "\"volume\":" + c / (double) VOL_STEPS));
    }

    private void cmd(String command, String args) {
        try {
            http("POST", "/api/music/cmd", "{\"command\":\"" + command + "\"" + (args == null ? "" : "," + args) + "}");
        } catch (IOException e) {
            Log.w(PanelService.TAG, "команда " + command + ": " + e);
        }
        bg.removeCallbacks(poll);
        bg.postDelayed(poll, 400);   // кнопка нажата — быстро показываем новое состояние
    }

    private static String http(String method, String path, String body) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL("http://127.0.0.1:" + PanelService.PORT + path).openConnection();
        c.setConnectTimeout(1500);
        c.setReadTimeout(10000);
        c.setRequestMethod(method);
        if (body != null) {
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            try (OutputStream o = c.getOutputStream()) {
                o.write(body.getBytes("UTF-8"));
            }
        }
        try (InputStream in = c.getResponseCode() < 400 ? c.getInputStream() : c.getErrorStream()) {
            if (in == null) return "{}";
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[16384];
            for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
            return out.toString("UTF-8");
        } finally {
            c.disconnect();
        }
    }

    private static Bitmap loadCover(String url) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
            c.setConnectTimeout(5000);
            c.setReadTimeout(10000);
            try (InputStream in = c.getInputStream()) {
                return BitmapFactory.decodeStream(in);
            } finally {
                c.disconnect();
            }
        } catch (IOException e) {
            return null;
        }
    }
}
