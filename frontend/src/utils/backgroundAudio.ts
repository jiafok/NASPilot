/** Native background playback bridge.
 *
 * Why: the player is an HTML `<video>`/`<audio>` element inside a WebView, and
 * Android suspends a WebView — including its media — the moment the app leaves
 * the foreground. Pressing Home therefore stopped the lesson. A WebView has no
 * API to keep decoding in the background, so the Android app ships a small
 * foreground service (`PlaybackService`) with its own MediaPlayer and a media
 * notification, and playback is handed over to it when the app is paused.
 *
 * On the web build every function here is a no-op: browsers keep their own tab
 * audio alive, so there is nothing to hand over.
 */

import { Capacitor, registerPlugin } from '@capacitor/core';

export interface BackgroundPlaybackState {
  /** The service exists and holds a media player. */
  active: boolean;
  playing: boolean;
  /** Milliseconds. */
  position: number;
  /** Milliseconds. */
  duration: number;
  ended: boolean;
  error: string;
}

interface BackgroundAudioPlugin {
  start(options: { url: string; title: string; position: number }): Promise<{ started: boolean }>;
  pause(): Promise<BackgroundPlaybackState>;
  resume(): Promise<BackgroundPlaybackState>;
  seek(options: { position: number }): Promise<BackgroundPlaybackState>;
  stop(): Promise<BackgroundPlaybackState>;
  getState(): Promise<BackgroundPlaybackState>;
}

const BackgroundAudio = registerPlugin<BackgroundAudioPlugin>('BackgroundAudio');

const IDLE: BackgroundPlaybackState = {
  active: false,
  playing: false,
  position: 0,
  duration: 0,
  ended: false,
  error: '',
};

/** True on the Android app, where the handover is needed and available. */
export function hasBackgroundPlayback(): boolean {
  return Capacitor.isNativePlatform() && Capacitor.getPlatform() === 'android';
}

/** Hand playback over to the foreground service. */
export async function startBackgroundPlayback(options: {
  url: string;
  title: string;
  positionMs: number;
}): Promise<boolean> {
  if (!hasBackgroundPlayback()) return false;
  try {
    await BackgroundAudio.start({
      url: options.url,
      title: options.title,
      position: options.positionMs,
    });
    return true;
  } catch {
    // Starting a foreground service can still be refused (e.g. battery saver on
    // some ROMs). The caller keeps the WebView engine, which is no worse than
    // before this feature existed.
    return false;
  }
}

/** Read where the background engine got to, without stopping it. */
export async function backgroundPlaybackState(): Promise<BackgroundPlaybackState> {
  if (!hasBackgroundPlayback()) return IDLE;
  try {
    return await BackgroundAudio.getState();
  } catch {
    return IDLE;
  }
}

/** Stop the background engine and release the notification. */
export async function stopBackgroundPlayback(): Promise<BackgroundPlaybackState> {
  if (!hasBackgroundPlayback()) return IDLE;
  try {
    return await BackgroundAudio.stop();
  } catch {
    return IDLE;
  }
}

/** Pause the background engine (returns the state afterwards). */
export async function pauseBackgroundPlayback(): Promise<BackgroundPlaybackState> {
  if (!hasBackgroundPlayback()) return IDLE;
  try {
    return await BackgroundAudio.pause();
  } catch {
    return IDLE;
  }
}

/** Resume the background engine. */
export async function resumeBackgroundPlayback(): Promise<BackgroundPlaybackState> {
  if (!hasBackgroundPlayback()) return IDLE;
  try {
    return await BackgroundAudio.resume();
  } catch {
    return IDLE;
  }
}

/** Seek the background engine. */
export async function seekBackgroundPlayback(positionMs: number): Promise<BackgroundPlaybackState> {
  if (!hasBackgroundPlayback()) return IDLE;
  try {
    return await BackgroundAudio.seek({ position: positionMs });
  } catch {
    return IDLE;
  }
}
