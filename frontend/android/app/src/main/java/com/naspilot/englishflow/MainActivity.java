package com.naspilot.englishflow;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Local plugin: background playback lives in this app, not in a package.
        registerPlugin(BackgroundAudioPlugin.class);
        super.onCreate(savedInstanceState);
    }

    @Override
    public void onPause() {
        // Tell the web player it is about to lose the foreground *before*
        // `onStop`, while this activity still counts as visible: Android 12+
        // refuses to start a foreground service from the background, and the
        // handover to PlaybackService has to happen inside that window.
        if (bridge != null) {
            bridge.triggerWindowJSEvent("efAppPausing");
        }
        super.onPause();
    }

    @Override
    public void onResume() {
        super.onResume();
        if (bridge != null) {
            // Lets the player take playback back at the position the service
            // reached, so the two engines are never audible at once.
            bridge.triggerWindowJSEvent("efAppResumed");
        }
    }
}
