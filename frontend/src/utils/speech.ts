/** Speak English text out loud, on both the web build and the Android app.
 *
 * Why this module exists: the word card called `window.speechSynthesis`
 * directly. That works in a browser, but **Android WebView does not implement
 * the Web Speech API at all** — `window.speechSynthesis` is `undefined`, so the
 * "朗读" button did nothing and reported no error. The native app therefore
 * needs Capacitor's TTS plugin, and the browser still wants speechSynthesis.
 *
 * One more thing the old code got wrong: it never awaited or checked anything.
 * A device with no English TTS voice silent-fails the same way an unsupported
 * platform does, so every path here returns a reason the UI can show.
 */

import { Capacitor } from '@capacitor/core';
import { TextToSpeech } from '@capacitor-community/text-to-speech';

export type SpeakResult =
  | { ok: true }
  | { ok: false; reason: 'unsupported' | 'no-voice' | 'busy' | 'error'; message: string };

/** True when this build can actually make a sound. */
export function canSpeak(): boolean {
  if (Capacitor.isNativePlatform()) return true;
  return typeof window !== 'undefined' && 'speechSynthesis' in window;
}

/**
 * The language tag to speak `lang` in, or `null` when the device cannot.
 *
 * Falls back from `en-US` to any installed `en-*` voice: a British-only device
 * can still read the text, just with a different accent.
 */
async function resolveNativeLanguage(lang: string): Promise<string | null> {
  try {
    const wanted = await TextToSpeech.isLanguageSupported({ lang });
    if (wanted) return lang;
  } catch {
    // Older plugin builds may not expose the check; fall through to the list.
  }
  try {
    const { languages } = await TextToSpeech.getSupportedLanguages();
    const prefix = lang.split('-')[0].toLowerCase();
    const english = (languages || []).find(
      (item) => item.toLowerCase() === lang.toLowerCase() || item.toLowerCase().startsWith(prefix),
    );
    return english ?? null;
  } catch {
    // No way to tell — let `speak()` try rather than refusing outright.
    return lang;
  }
}

/**
 * Speak one word or sentence.
 *
 * `lang` defaults to `en-US`: the library is American/British English, and the
 * device default voice is usually Chinese, which would read English text with
 * Chinese pronunciation (or not at all).
 */
export async function speak(text: string, lang = 'en-US'): Promise<SpeakResult> {
  const value = (text || '').trim();
  if (!value) return { ok: false, reason: 'error', message: '没有可朗读的内容' };

  if (Capacitor.isNativePlatform()) {
    try {
      // Ask the engine first. `speak()` happily resolves on a device with no
      // English voice installed while playing nothing at all — which is exactly
      // the "no sound and no error" report this module was written for.
      const resolved = await resolveNativeLanguage(lang);
      if (!resolved) {
        return {
          ok: false,
          reason: 'no-voice',
          message:
            '手机上没有可用的英语朗读引擎。请在「设置 → 语言和输入法 → 文字转语音」中安装或启用 Google 语音服务，并下载英语语音包。',
        };
      }

      // `TextToSpeech.stop()` first: tapping a second word while the first is
      // still being spoken otherwise queues them and the learner waits.
      await TextToSpeech.stop().catch(() => undefined);
      await TextToSpeech.speak({
        text: value,
        lang: resolved,
        rate: 0.95,
        pitch: 1.0,
        volume: 1.0,
        category: 'ambient',
      });
      return { ok: true };
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      // The plugin throws when no installed engine has a voice for the language.
      const noVoice = /voice|engine|lang|not\s*found|missing|tts/i.test(message);
      return {
        ok: false,
        reason: noVoice ? 'no-voice' : 'error',
        message: noVoice
          ? '手机上没有可用的英语朗读引擎。请在「设置 → 语言和输入法 → 文字转语音」中安装或启用 Google 语音服务。'
          : `朗读失败：${message}`,
      };
    }
  }

  if (typeof window === 'undefined' || !('speechSynthesis' in window)) {
    return { ok: false, reason: 'unsupported', message: '当前环境不支持朗读' };
  }

  try {
    // Same reason as the native stop(): browsers queue utterances.
    window.speechSynthesis.cancel();

    // Voices load asynchronously in Chrome; speaking before they arrive is a
    // silent no-op, which is exactly the bug this function replaces.
    let voices = window.speechSynthesis.getVoices();
    if (voices.length === 0) {
      voices = await new Promise<SpeechSynthesisVoice[]>((resolve) => {
        const done = () => resolve(window.speechSynthesis.getVoices());
        window.speechSynthesis.addEventListener('voiceschanged', done, { once: true });
        window.setTimeout(done, 600);
      });
    }

    const prefix = lang.split('-')[0].toLowerCase();
    const voice = voices.find((item) => item.lang?.toLowerCase() === lang.toLowerCase())
      ?? voices.find((item) => item.lang?.toLowerCase().startsWith(prefix));

    const utterance = new SpeechSynthesisUtterance(value);
    utterance.lang = lang;
    if (voice) utterance.voice = voice;
    utterance.rate = 0.95;

    // Resolve on `end`/`error` so the caller can show a real failure instead of
    // assuming a silent call worked.
    return await new Promise<SpeakResult>((resolve) => {
      let settled = false;
      const finish = (result: SpeakResult) => {
        if (settled) return;
        settled = true;
        resolve(result);
      };
      utterance.onend = () => finish({ ok: true });
      utterance.onerror = (event) => {
        const error = (event as SpeechSynthesisErrorEvent).error || 'error';
        finish({
          ok: false,
          reason: error === 'voice-unavailable' ? 'no-voice' : 'error',
          message: `朗读失败：${error}`,
        });
      };
      window.speechSynthesis.speak(utterance);
      // Some engines never fire `end`; do not leave the button spinning forever.
      window.setTimeout(() => finish({ ok: true }), Math.max(3000, value.length * 120));
    });
  } catch (reason) {
    return {
      ok: false,
      reason: 'error',
      message: reason instanceof Error ? reason.message : '朗读失败',
    };
  }
}

/** Stop anything currently being spoken. */
export async function stopSpeaking(): Promise<void> {
  if (Capacitor.isNativePlatform()) {
    await TextToSpeech.stop().catch(() => undefined);
    return;
  }
  if (typeof window !== 'undefined' && 'speechSynthesis' in window) {
    window.speechSynthesis.cancel();
  }
}
