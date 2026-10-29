package com.naspilot.englishflow;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

import androidx.core.content.ContextCompat;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/**
 * JS bridge for {@link PlaybackService}.
 *
 * The web player calls `start()` from a native "about to pause" event, i.e.
 * while the activity is still visible, so Android's background
 * foreground-service-start restriction (API 31+) never applies. `getState()`
 * is what lets the player resume the WebView element at the exact position the
 * service reached while the screen was off.
 */
@CapacitorPlugin(name = "BackgroundAudio")
public class BackgroundAudioPlugin extends Plugin {

    @PluginMethod
    public void start(PluginCall call) {
        String url = call.getString("url");
        if (url == null || url.isEmpty()) {
            call.reject("缺少播放地址");
            return;
        }
        String title = call.getString("title", "EnglishFlow");
        int position = call.getInt("position", 0);

        requestNotificationPermissionIfNeeded();

        Intent intent = new Intent(getContext(), PlaybackService.class);
        intent.setAction(PlaybackService.ACTION_START);
        intent.putExtra(PlaybackService.EXTRA_URL, url);
        intent.putExtra(PlaybackService.EXTRA_TITLE, title);
        intent.putExtra(PlaybackService.EXTRA_POSITION, position);
        try {
            ContextCompat.startForegroundService(getContext(), intent);
        } catch (Exception exception) {
            call.reject("无法开始后台播放：" + describe(exception));
            return;
        }
        JSObject result = new JSObject();
        result.put("started", true);
        call.resolve(result);
    }

    @PluginMethod
    public void pause(PluginCall call) {
        PlaybackService service = PlaybackService.current;
        if (service != null) {
            service.pausePlayback();
        }
        call.resolve(state());
    }

    @PluginMethod
    public void resume(PluginCall call) {
        PlaybackService service = PlaybackService.current;
        if (service != null) {
            service.resumePlayback();
        }
        call.resolve(state());
    }

    @PluginMethod
    public void seek(PluginCall call) {
        PlaybackService service = PlaybackService.current;
        Integer position = call.getInt("position");
        if (service != null && position != null) {
            service.seekTo(position);
        }
        call.resolve(state());
    }

    @PluginMethod
    public void stop(PluginCall call) {
        Intent intent = new Intent(getContext(), PlaybackService.class);
        intent.setAction(PlaybackService.ACTION_STOP);
        try {
            getContext().startService(intent);
        } catch (Exception exception) {
            // Service already gone — stopping twice is not an error for the UI.
        }
        call.resolve(state());
    }

    @PluginMethod
    public void getState(PluginCall call) {
        call.resolve(state());
    }

    /**
     * Snapshot of the service.
     *
     * `position` lets the WebView player resume where the background engine
     * stopped; `ended` / `error` let it explain why nothing is playing instead
     * of silently rewinding to the start.
     */
    private JSObject state() {
        PlaybackService service = PlaybackService.current;
        JSObject result = new JSObject();
        result.put("active", service != null);
        result.put("playing", service != null && service.isPlaying());
        result.put("position", service == null ? 0 : service.getPositionMs());
        result.put("duration", service == null ? 0 : service.getDurationMs());
        result.put("ended", service != null && service.isEnded());
        result.put("error", service == null ? "" : service.getError());
        return result;
    }

    private void requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
            return;
        }
        if (ContextCompat.checkSelfPermission(getContext(), Manifest.permission.POST_NOTIFICATIONS)
                == PackageManager.PERMISSION_GRANTED) {
            return;
        }
        try {
            getActivity().requestPermissions(
                new String[] { Manifest.permission.POST_NOTIFICATIONS }, 7101
            );
        } catch (Exception ignored) {
            // Playback still works; only the notification stays hidden.
        }
    }

    private String describe(Exception exception) {
        String message = exception.getMessage();
        return message == null || message.isEmpty()
            ? exception.getClass().getSimpleName()
            : message;
    }
}
