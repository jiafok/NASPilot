package com.naspilot.englishflow;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.AudioAttributes;
import android.media.AudioManager;
import android.media.MediaPlayer;
import android.os.Build;
import android.os.IBinder;
import android.os.PowerManager;
import android.util.Log;

import androidx.core.app.NotificationCompat;

/**
 * Background playback for the EnglishFlow learner.
 *
 * Why this exists: the app's player is an HTML <video>/<audio> element inside a
 * WebView. Android suspends a WebView (and its media) as soon as the app leaves
 * the foreground, so pressing Home stopped the lesson. A WebView has no API to
 * keep decoding in the background — this is what every audio app does instead:
 * a foreground service with its own MediaPlayer and a media notification.
 *
 * The web player hands over its URL and position when the app is paused and
 * takes playback back (at the same position) when the user returns, so the two
 * engines are never audible at the same time.
 */
public class PlaybackService extends Service {

    private static final String TAG = "EfPlayback";

    public static final String ACTION_START = "com.naspilot.englishflow.PLAY";
    public static final String ACTION_TOGGLE = "com.naspilot.englishflow.TOGGLE";
    public static final String ACTION_STOP = "com.naspilot.englishflow.STOP";

    public static final String EXTRA_URL = "url";
    public static final String EXTRA_TITLE = "title";
    public static final String EXTRA_POSITION = "position";

    private static final String CHANNEL_ID = "ef_playback";
    private static final int NOTIFICATION_ID = 4101;

    /** Single-player app: the plugin reads live state straight from here. */
    public static volatile PlaybackService current;

    private MediaPlayer player;
    private String title = "";
    private boolean prepared;
    private boolean ended;
    private String error = "";
    private PowerManager.WakeLock wakeLock;

    public static boolean isActive() {
        PlaybackService service = current;
        return service != null && service.player != null;
    }

    @Override
    public void onCreate() {
        super.onCreate();
        current = this;
        createChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) {
            return START_NOT_STICKY;
        }
        String action = intent.getAction();
        if (ACTION_STOP.equals(action)) {
            releasePlayer();
            stopForegroundCompat();
            stopSelf();
            return START_NOT_STICKY;
        }
        if (ACTION_TOGGLE.equals(action)) {
            toggleFromNotification();
            return START_NOT_STICKY;
        }
        if (ACTION_START.equals(action)) {
            String url = intent.getStringExtra(EXTRA_URL);
            title = intent.getStringExtra(EXTRA_TITLE) == null ? "EnglishFlow" : intent.getStringExtra(EXTRA_TITLE);
            int position = intent.getIntExtra(EXTRA_POSITION, 0);
            startPlayback(url, position);
        }
        return START_NOT_STICKY;
    }

    private void startPlayback(String url, int position) {
        if (url == null || url.isEmpty()) {
            error = "缺少播放地址";
            stopSelf();
            return;
        }
        releasePlayer();
        ended = false;
        error = "";
        try {
            player = new MediaPlayer();
            player.setAudioAttributes(
                new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            );
            player.setDataSource(url);
            player.setOnPreparedListener(mp -> {
                prepared = true;
                if (position > 0) {
                    mp.seekTo(position);
                }
                mp.start();
                acquireWakeLock();
                showNotification(true);
                startForegroundCompat();
            });
            player.setOnCompletionListener(mp -> {
                ended = true;
                releasePlayer();
                stopForegroundCompat();
                stopSelf();
            });
            player.setOnErrorListener((mp, what, extra) -> {
                error = "无法播放该音频（错误 " + what + "/" + extra + "）";
                Log.w(TAG, error);
                releasePlayer();
                stopForegroundCompat();
                stopSelf();
                return true;
            });
            // A media notification must be up quickly after startForeground.
            showNotification(false);
            startForegroundCompat();
            player.prepareAsync();
        } catch (Exception exception) {
            error = exception.getMessage() == null ? "播放失败" : exception.getMessage();
            Log.w(TAG, "start failed", exception);
            releasePlayer();
            stopForegroundCompat();
            stopSelf();
        }
    }

    private void toggleFromNotification() {
        if (player == null || !prepared) {
            return;
        }
        if (player.isPlaying()) {
            player.pause();
            showNotification(false);
        } else {
            player.start();
            showNotification(true);
        }
    }

    public boolean isPlaying() {
        try {
            return player != null && prepared && player.isPlaying();
        } catch (IllegalStateException exception) {
            return false;
        }
    }

    public int getPositionMs() {
        try {
            return player == null ? 0 : player.getCurrentPosition();
        } catch (IllegalStateException exception) {
            return 0;
        }
    }

    public int getDurationMs() {
        try {
            return player == null ? 0 : player.getDuration();
        } catch (IllegalStateException exception) {
            return 0;
        }
    }

    public boolean isEnded() {
        return ended;
    }

    public String getError() {
        return error;
    }

    public void pausePlayback() {
        if (player != null && prepared && player.isPlaying()) {
            player.pause();
            showNotification(false);
        }
    }

    public void resumePlayback() {
        if (player != null && prepared) {
            player.start();
            showNotification(true);
        }
    }

    public void seekTo(int positionMs) {
        if (player != null && prepared) {
            player.seekTo(positionMs);
        }
    }

    // ── notification ───────────────────────────────────────────────────

    private void createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager == null || manager.getNotificationChannel(CHANNEL_ID) != null) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
            CHANNEL_ID, "后台播放", NotificationManager.IMPORTANCE_LOW
        );
        channel.setDescription("锁屏或返回桌面时继续播放课程");
        channel.setShowBadge(false);
        manager.createNotificationChannel(channel);
    }

    private void showNotification(boolean playing) {
        Notification notification = buildNotification(playing);
        NotificationManager manager = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (manager != null) {
            manager.notify(NOTIFICATION_ID, notification);
        }
    }

    private Notification buildNotification(boolean playing) {
        Intent open = new Intent(this, MainActivity.class);
        open.setFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        PendingIntent contentIntent = PendingIntent.getActivity(
            this, 0, open,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        Intent toggle = new Intent(this, PlaybackService.class).setAction(ACTION_TOGGLE);
        PendingIntent toggleIntent = PendingIntent.getService(
            this, 1, toggle,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        Intent stop = new Intent(this, PlaybackService.class).setAction(ACTION_STOP);
        PendingIntent stopIntent = PendingIntent.getService(
            this, 2, stop,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
        );

        return new NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle(title)
            .setContentText(playing ? "正在后台播放" : "已暂停")
            .setContentIntent(contentIntent)
            .setOngoing(playing)
            .setOnlyAlertOnce(true)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .addAction(
                playing ? android.R.drawable.ic_media_pause : android.R.drawable.ic_media_play,
                playing ? "暂停" : "继续",
                toggleIntent
            )
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, "停止", stopIntent)
            .build();
    }

    private void startForegroundCompat() {
        Notification notification = buildNotification(isPlaying());
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK);
        } else {
            startForeground(NOTIFICATION_ID, notification);
        }
    }

    private void stopForegroundCompat() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(Service.STOP_FOREGROUND_REMOVE);
        } else {
            stopForeground(true);
        }
    }

    // ── housekeeping ───────────────────────────────────────────────────

    private void acquireWakeLock() {
        if (wakeLock != null) {
            return;
        }
        PowerManager manager = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (manager == null) {
            return;
        }
        wakeLock = manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "EnglishFlow::playback");
        wakeLock.setReferenceCounted(false);
        wakeLock.acquire();
    }

    private void releaseWakeLock() {
        if (wakeLock != null && wakeLock.isHeld()) {
            wakeLock.release();
        }
        wakeLock = null;
    }

    private void releasePlayer() {
        prepared = false;
        if (player != null) {
            try {
                player.reset();
                player.release();
            } catch (Exception exception) {
                Log.w(TAG, "release failed", exception);
            }
            player = null;
        }
        releaseWakeLock();
    }

    @Override
    public void onDestroy() {
        releasePlayer();
        if (current == this) {
            current = null;
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    /** Route this device's media buttons to the notification actions. */
    public AudioManager audioManager() {
        return (AudioManager) getSystemService(Context.AUDIO_SERVICE);
    }
}
