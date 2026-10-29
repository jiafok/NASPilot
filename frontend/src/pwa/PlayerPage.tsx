import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  apiGet,
  apiPost,
  errText,
  fmtTime,
  withToken,
  type LessonDetail,
  type Segment,
  type WordEntry,
} from './api';
import {
  IconBack,
  IconBack15,
  IconCaptions,
  IconClose,
  IconExpand,
  IconFloat,
  IconForward15,
  IconHeadphones,
  IconList,
  IconPause,
  IconPlay,
  IconShrink,
  IconSpeed,
  IconStar,
} from './components';
import { hasWord, saveWord } from './wordStore';
import { ArrowUpOutlined } from '@ant-design/icons';

/**
 * Cue index for a playback position.
 *
 * Two behaviours matter and both were wrong in the naive version:
 * - gaps between cues keep the previous cue highlighted, otherwise the
 *   highlight flickers off on every pause in speech;
 * - a new cue must be seen twice before it wins, so a fast seek does not make
 *   the highlight jump backwards for a frame.
 */
class SubtitleSync {
  private active = -1;
  private stable = -1;
  private candidate = -1;
  private hits = 0;
  private readonly cues: Segment[];

  constructor(cues: Segment[]) {
    this.cues = cues;
  }

  private indexAt(ms: number): number {
    const cues = this.cues;
    let lo = 0;
    let hi = cues.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      const cue = cues[mid];
      if (ms < cue.start) hi = mid - 1;
      else if (ms >= cue.end) lo = mid + 1;
      else return mid;
    }
    return this.stable;
  }

  /** Jump straight to the cue at ``ms`` (used by seek, not by playback). */
  seek(ms: number): number {
    const index = this.indexAt(ms);
    this.active = index;
    this.stable = index;
    this.candidate = index;
    this.hits = 0;
    return index;
  }

  reset() {
    this.active = -1;
    this.stable = -1;
    this.candidate = -1;
    this.hits = 0;
  }

  update(ms: number): number {
    const next = this.indexAt(ms);
    if (next === this.active) {
      this.hits = 0;
      return this.active;
    }
    if (next === this.candidate) this.hits += 1;
    else {
      this.candidate = next;
      this.hits = 1;
    }
    if (this.hits >= 2) {
      this.active = next;
      this.stable = next;
    }
    return this.active;
  }
}

interface Props {
  lessonId: number;
  minimized: boolean;
  onClose: () => void;
  onExpand: () => void;
  onStop: () => void;
  /** Continue to another lesson without leaving the player. */
  onOpenLesson: (lessonId: number) => void;
}

const SPEEDS = [0.75, 1, 1.25, 1.5];

/** How much screen the video takes. */
type DisplayMode = 'normal' | 'max' | 'float' | 'audio';

/** Controls hide themselves again after this long while playing. */
const CONTROLS_HIDE_MS = 3500;

/** A horizontal flick shorter than this is a tap, not a swipe. */
const SWIPE_MIN_PX = 70;

/**
 * Which way a gesture points, or ``null`` for "not a navigation".
 *
 * Pulled out of the handlers so touch and pointer input share one rule, and so
 * the rule can be exercised without a browser: a vertical drag (the
 * transcript scrolling) must never change lesson.
 *
 * Deliberately no time limit. A duration check looked sensible but rejected
 * real swipes: with 400 subtitle cues and a video on screen the main thread can
 * be busy for most of a second, so the measured gap between pointerdown and
 * pointerup was ~1s for a gesture the user performed in 200ms.
 */
export function swipeDirection(dx: number, dy: number): 'next' | 'prev' | null {
  if (Math.abs(dx) < SWIPE_MIN_PX) return null;
  if (Math.abs(dx) < Math.abs(dy) * 1.5) return null;
  return dx < 0 ? 'next' : 'prev';
}

export default function PlayerPage({ lessonId, minimized, onClose, onExpand, onStop, onOpenLesson }: Props) {
  const [lesson, setLesson] = useState<LessonDetail | null>(null);
  const [cues, setCues] = useState<Segment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [repeatTarget, setRepeatTarget] = useState(1);
  const [completedPlays, setCompletedPlays] = useState(0);
  const [active, setActive] = useState(-1);
  const [speed, setSpeed] = useState(1);
  // The caption in the picture and the transcript list are separate switches:
  // "burned-in line without the list" and "list with a clean picture" are both
  // normal ways to study, and one switch for both was the complaint.
  const [captionOn, setCaptionOn] = useState(true);
  const [listOn, setListOn] = useState(true);
  const [controlsOn, setControlsOn] = useState(true);
  const [displayMode, setDisplayMode] = useState<DisplayMode>('normal');
  const [wordSheet, setWordSheet] = useState<{ word: string; sentence: string } | null>(null);
  const [follow, setFollow] = useState(true);
  const [showReturnTop, setShowReturnTop] = useState(false);
  const [stalled, setStalled] = useState(false);

  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null);
  const subsRef = useRef<HTMLDivElement | null>(null);
  const syncRef = useRef<SubtitleSync | null>(null);
  const cueRefs = useRef<Array<HTMLElement | null>>([]);
  const reportedRef = useRef(0);
  const listenedRef = useRef(0);
  const lastTickRef = useRef(0);
  const followTimerRef = useRef<number | null>(null);
  const autoScrollAtRef = useRef(0);
  const lastScrollTopRef = useRef(0);
  const hideTimerRef = useRef<number | null>(null);
  const touchRef = useRef<{ x: number; y: number } | null>(null);

  // ── load ───────────────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    reportedRef.current = 0;
    listenedRef.current = 0;

    (async () => {
      try {
        const detail = await apiGet.lesson(lessonId);
        if (cancelled) return;
        setLesson(detail);
        setDuration(detail.duration || 0);
        setRepeatTarget(detail.progress.repeat_target || 1);
        setCompletedPlays(detail.progress.completed_plays || 0);
        if (detail.has_subtitle) {
          const tr = await apiGet.transcript(lessonId);
          if (cancelled) return;
          const sorted = [...tr.segments].sort((a, b) => a.start - b.start);
          setCues(sorted);
          syncRef.current = new SubtitleSync(sorted);
        } else {
          setCues([]);
          syncRef.current = null;
        }
      } catch (err) {
        if (!cancelled) setError(errText(err, '无法打开这一课，请检查网络'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [lessonId]);

  // ── progress reporting ────────────────────────────────────────────────
  // Reported on a fixed cadence plus on pause/exit. The backend accumulates
  // `listen_seconds`, so each call sends the delta since the previous one.
  const report = useCallback(
    (options: { completed?: boolean; repeatTarget?: number } = {}) => {
      if (!lesson) return;
      const media = mediaRef.current;
      const current = media?.currentTime ?? position;
      const delta = Math.max(0, Math.round(listenedRef.current));
      if (!delta && options.completed === undefined && options.repeatTarget === undefined) return;
      listenedRef.current = 0;
      void apiPost
        .progress({
          lesson_id: lesson.id,
          position: Math.round(current * 10) / 10,
          listen_seconds: delta,
          repeat_target: options.repeatTarget ?? repeatTarget,
          ...(options.completed === undefined ? {} : { completed: options.completed }),
        })
        .then(({ data }) => {
          if (data.completed_plays != null) setCompletedPlays(data.completed_plays);
          if (options.completed !== undefined || options.repeatTarget !== undefined) {
            window.dispatchEvent(new Event('english-progress-updated'));
          }
        })
        .catch(() => {
          // Never interrupt playback for a failed heartbeat.
          listenedRef.current += delta;
        });
    },
    [lesson, position, repeatTarget],
  );

  const reportRef = useRef(report);
  reportRef.current = report;

  useEffect(() => {
    const id = window.setInterval(() => {
      const media = mediaRef.current;
      if (!media || media.paused) return;
      reportRef.current();
    }, 10000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    // Read the reporter through a ref: with `report` in the deps this cleanup
    // re-ran on every position change, i.e. one POST per timeupdate.
    return () => {
      reportRef.current();
    };
  }, []);

  // ── media events ──────────────────────────────────────────────────────
  // Some files in a YouTube-derived library are AV1 video or Opus audio, and a
  // phone that cannot decode them just stops advancing without raising an
  // `error` event. Saying so is far better than an apparently frozen player the
  // user blames on the app.
  const stallTimerRef = useRef<number | null>(null);
  useEffect(() => {
    if (!playing) {
      setStalled(false);
      if (stallTimerRef.current) window.clearTimeout(stallTimerRef.current);
      return;
    }
    const startAt = mediaRef.current?.currentTime ?? 0;
    stallTimerRef.current = window.setTimeout(() => {
      const media = mediaRef.current;
      if (!media) return;
      if (!media.paused && Math.abs(media.currentTime - startAt) < 0.3) setStalled(true);
    }, 6000);
    return () => {
      if (stallTimerRef.current) window.clearTimeout(stallTimerRef.current);
    };
  }, [playing]);

  /**
   * Scroll a cue to the middle of the list.
   *
   * `offsetTop` is relative to the nearest *positioned* ancestor, which here is
   * `.ef-player-inner`, not the scroll container — so the old formula scrolled
   * to the wrong place and the active line stayed off screen after a seek.
   */
  const centreCue = useCallback((index: number, behavior: ScrollBehavior) => {
    const node = cueRefs.current[index];
    const container = subsRef.current;
    if (!node || !container) return;
    const containerRect = container.getBoundingClientRect();
    const nodeRect = node.getBoundingClientRect();
    const top =
      container.scrollTop +
      (nodeRect.top - containerRect.top) -
      container.clientHeight / 2 +
      nodeRect.height / 2;
    autoScrollAtRef.current = Date.now();
    container.scrollTo({ top: Math.max(0, top), behavior });
  }, []);

  const onTimeUpdate = useCallback(() => {
    const media = mediaRef.current;
    if (!media) return;
    const now = media.currentTime;
    setPosition(now);

    // Accumulate listened time only while actually playing (seeking must not
    // inflate the study statistics).
    if (!media.paused) {
      const gap = now - lastTickRef.current;
      if (gap > 0 && gap < 2) listenedRef.current += gap;
    }
    lastTickRef.current = now;

    const sync = syncRef.current;
    if (sync) {
      const next = sync.update(now * 1000);
      if (next !== active) setActive(next);
    }
  }, [active]);

  // Keep the active cue vertically centred, but only while the user is not
  // scrolling the list themselves.
  useEffect(() => {
    if (!follow || active < 0) return;
    // Mark the moment we scroll ourselves: the resulting `scroll` event would
    // otherwise be mistaken for the user taking over and would switch follow
    // off after the very first line.
    centreCue(active, 'smooth');
  }, [active, follow, centreCue]);

  const pauseFollow = useCallback(() => {
    // Ignore the scroll events our own animation produces.
    if (Date.now() - autoScrollAtRef.current < 700) return;
    setFollow(false);
    if (followTimerRef.current) window.clearTimeout(followTimerRef.current);
    if (playing) followTimerRef.current = window.setTimeout(() => setFollow(true), 3000);
  }, [playing]);

  const onSubtitleScroll = useCallback(() => {
    const container = subsRef.current;
    if (!container) return;
    const nextTop = container.scrollTop;
    if (Date.now() - autoScrollAtRef.current >= 700 && Math.abs(nextTop - lastScrollTopRef.current) > 2) {
      setShowReturnTop(nextTop < lastScrollTopRef.current);
      pauseFollow();
    }
    lastScrollTopRef.current = nextTop;
  }, [pauseFollow]);

  useEffect(() => {
    if (playing) {
      setFollow(true);
      return;
    }
    if (followTimerRef.current) window.clearTimeout(followTimerRef.current);
  }, [playing]);

  /**
   * Seek and highlight in the same tick.
   *
   * Playback needs a two-hit debounce so the highlight does not flicker, but a
   * seek is an explicit jump: waiting for a second `timeupdate` left the line
   * being read unhighlighted (and unscrolled), which is exactly when the user
   * is looking for it.
   */
  const jumpTo = useCallback(
    (seconds: number, options: { play?: boolean } = {}) => {
      const media = mediaRef.current;
      if (!media) return;
      const target = Math.max(0, Math.min(media.duration || duration || 0, seconds));
      media.currentTime = target;
      setPosition(target);
      const sync = syncRef.current;
      if (sync) {
        const index = sync.seek(target * 1000);
        setActive(index);
        centreCue(index, 'auto');
        setFollow(true);
      }
      if (options.play) void media.play().catch(() => undefined);
    },
    [duration],
  );

  const selectCue = useCallback((cue: Segment, index: number) => {
    if (playing) {
      jumpTo(cue.start / 1000, { play: true });
      return;
    }
    setActive(index);
    setFollow(false);
    centreCue(index, 'smooth');
  }, [centreCue, jumpTo, playing]);

  const togglePlay = useCallback(() => {
    const media = mediaRef.current;
    if (!media) return;
    if (media.paused) void media.play().catch(() => undefined);
    else media.pause();
  }, []);

  const stopPlayback = useCallback(() => {
    mediaRef.current?.pause();
    report();
    onStop();
  }, [onStop, report]);

  const skip = useCallback(
    (seconds: number) => {
      const media = mediaRef.current;
      if (!media) return;
      jumpTo((media.currentTime || 0) + seconds);
    },
    [jumpTo],
  );

  const cycleSpeed = useCallback(() => {
    const index = SPEEDS.indexOf(speed);
    const next = SPEEDS[(index + 1) % SPEEDS.length];
    setSpeed(next);
    if (mediaRef.current) mediaRef.current.playbackRate = next;
  }, [speed]);

  // ── controls overlay ───────────────────────────────────────────────────
  // A fixed control panel under the video ate a third of the screen and
  // pushed the transcript away. The controls now sit on the picture, appear on
  // tap, and get out of the way again.
  const scheduleHide = useCallback(() => {
    if (hideTimerRef.current) window.clearTimeout(hideTimerRef.current);
    const media = mediaRef.current;
    // A paused player keeps its controls: hiding them would strand the user.
    if (media && media.paused) return;
    hideTimerRef.current = window.setTimeout(() => setControlsOn(false), CONTROLS_HIDE_MS);
  }, []);

  const onStageTap = useCallback(() => {
    if (controlsOn) {
      if (hideTimerRef.current) window.clearTimeout(hideTimerRef.current);
      setControlsOn(false);
      return;
    }
    setControlsOn(true);
    scheduleHide();
  }, [controlsOn, scheduleHide]);

  useEffect(() => {
    if (!playing) {
      if (hideTimerRef.current) window.clearTimeout(hideTimerRef.current);
      setControlsOn(true);
      return;
    }
    scheduleHide();
  }, [playing, scheduleHide]);

  useEffect(
    () => () => {
      if (hideTimerRef.current) window.clearTimeout(hideTimerRef.current);
    },
    [],
  );

  // ── swipe left / right to change lesson ────────────────────────────────
  // The prev/next buttons sat on top of the 查词 button and could not be
  // hidden, so navigation became a gesture instead. Pointer events cover a
  // mouse (desktop PWA) with the same rule as touch.
  const gestureStart = useCallback((x: number, y: number) => {
    touchRef.current = { x, y };
  }, []);

  const gestureEnd = useCallback(
    (x: number, y: number) => {
      const start = touchRef.current;
      touchRef.current = null;
      if (!start) return;
      const direction = swipeDirection(x - start.x, y - start.y);
      if (!direction) return;
      const target = direction === 'next' ? lesson?.next_lesson_id : lesson?.prev_lesson_id;
      if (target) onOpenLesson(target);
    },
    [lesson, onOpenLesson],
  );

  // One document-level listener rather than a React prop or an element ref:
  // `touchstart` can stay passive (smooth scrolling), and there is no timing
  // dependency on when the player element appears.
  useEffect(() => {
    if (minimized) return;
    const ignored = (target: EventTarget | null) => {
      // A sheet (word card) or a dialog owns its own gestures.
      const element = target as HTMLElement | null;
      return Boolean(element?.closest?.('.ef-sheet, .ef-sheet-mask, .ant-modal'));
    };

    const onTouchStart = (event: globalThis.TouchEvent) => {
      if (ignored(event.target)) return;
      const touch = event.touches[0];
      if (touch) gestureStart(touch.clientX, touch.clientY);
    };
    const onTouchEnd = (event: globalThis.TouchEvent) => {
      if (ignored(event.target)) return;
      const touch = event.changedTouches[0];
      if (touch) gestureEnd(touch.clientX, touch.clientY);
    };
    const onPointerDown = (event: globalThis.PointerEvent) => {
      // A touch supplies both; only a mouse needs this path.
      if (event.pointerType === 'touch') return;
      if (ignored(event.target)) return;
      gestureStart(event.clientX, event.clientY);
    };
    const onPointerUp = (event: globalThis.PointerEvent) => {
      if (event.pointerType === 'touch') return;
      if (ignored(event.target)) return;
      gestureEnd(event.clientX, event.clientY);
    };

    document.addEventListener('touchstart', onTouchStart, { passive: true });
    document.addEventListener('touchend', onTouchEnd);
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('pointerup', onPointerUp);
    return () => {
      document.removeEventListener('touchstart', onTouchStart);
      document.removeEventListener('touchend', onTouchEnd);
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('pointerup', onPointerUp);
    };
  }, [gestureStart, gestureEnd, minimized]);

  // Arrow keys do the same thing for a keyboard: the gesture is the only way
  // to change lesson now that the buttons are gone, and a desktop PWA has no
  // touch screen to swipe on.
  useEffect(() => {
    if (minimized) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      const target = event.key === 'ArrowRight' ? lesson?.next_lesson_id : lesson?.prev_lesson_id;
      if (target) onOpenLesson(target);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [lesson, minimized, onOpenLesson]);

  const currentCue = active >= 0 ? cues[active] : null;

  const groupedCues = useMemo(() => {
    // A speaker label is shown only where the speaker changes, so a long turn
    // reads as one block instead of repeating "A" on every line.
    return cues.map((cue, index) => {
      const prev = index > 0 ? cues[index - 1] : null;
      const turnStart = !prev || prev.spk !== cue.spk;
      return { cue, showSpeaker: Boolean(cue.spk) && turnStart };
    });
  }, [cues]);

  const minimizePlayer = useCallback(() => {
    setWordSheet(null);
    onClose();
  }, [onClose]);

  if (loading) {
    return (
      <div className="ef-player" data-minimized={minimized}>
        <div className="ef-player-inner">
          <div className="ef-player-top">
            <button type="button" className="ef-icon-btn" onClick={minimizePlayer} aria-label="收起播放器">
              <IconBack />
            </button>
          </div>
          <div className="ef-skel" style={{ height: 160, marginTop: 40 }} />
          <div className="ef-skel" style={{ height: 22, marginTop: 24, width: '70%' }} />
          <div className="ef-skel" style={{ height: 120, marginTop: 32 }} />
        </div>
      </div>
    );
  }

  if (error || !lesson) {
    return (
      <div className="ef-player" data-minimized={minimized}>
        <div className="ef-player-inner">
          <div className="ef-player-top">
            <button type="button" className="ef-icon-btn" onClick={minimizePlayer} aria-label="收起播放器">
              <IconBack />
            </button>
          </div>
          <div className="ef-empty" style={{ color: 'rgba(248,250,252,0.7)' }}>
            <strong style={{ color: '#f8fafc' }}>无法播放</strong>
            <p>{error || '课时不存在'}</p>
            <button type="button" className="ef-btn" onClick={onClose}>
              返回
            </button>
          </div>
        </div>
      </div>
    );
  }

  const mediaProps = {
    ref: mediaRef as never,
    src: withToken(lesson.stream_url),
    onTimeUpdate,
    onLoadedMetadata: () => setDuration(mediaRef.current?.duration || lesson.duration || 0),
    onPlay: () => setPlaying(true),
    onPause: () => {
      setPlaying(false);
      report();
    },
    onEnded: () => {
      setPlaying(false);
      report({ completed: true });
    },
  };

  return (
    <div className="ef-player" data-minimized={minimized}>
      <div
        className="ef-player-bg"
        style={{ backgroundImage: `url(${withToken(lesson.cover)})` }}
        aria-hidden
      />
      <div className="ef-player-veil" aria-hidden />

      <div className="ef-player-inner">
        <div className="ef-player-mini-meta" onClick={onExpand} role="button" tabIndex={0} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') onExpand(); }}>
          <strong>{lesson.title}</strong>
          <span>{playing ? '正在播放' : '已暂停'} · {fmtTime(position)}</span>
        </div>
        <div className="ef-player-mini-controls">
          <button type="button" className="ef-mini-control" onClick={togglePlay} aria-label={playing ? '暂停' : '继续播放'}>{playing ? <IconPause /> : <IconPlay />}</button>
          <button type="button" className="ef-mini-control" onClick={onExpand} aria-label="展开播放器"><IconExpand /></button>
          <button type="button" className="ef-mini-control" onClick={stopPlayback} aria-label="停止并关闭播放器"><IconClose /></button>
        </div>
        <div className="ef-player-top">
            <button type="button" className="ef-icon-btn" onClick={minimizePlayer} aria-label="收起播放器">
            <IconBack />
          </button>
          <div className="ef-player-head">
            <h2>{lesson.title}</h2>
            <p>
              {lesson.level} · {lesson.format}
              {lesson.topics.length ? ` · ${lesson.topics.join(' / ')}` : ''}
            </p>
          </div>
          <button
            type="button"
            className="ef-icon-btn"
            onClick={() => {
              void apiPost.favorite(lesson.course_id, true).catch(() => undefined);
            }}
            aria-label="收藏"
          >
            <IconStar />
          </button>
        </div>

        {/* Media stage. The video, the burned-in caption and the tap-to-show
            controls all live here, so the transcript keeps the rest of the
            screen instead of being pushed away by a fixed panel. */}
        <div
          className={`ef-stage ef-stage--${displayMode}`}
          data-controls={controlsOn}
          onClick={minimized ? onExpand : onStageTap}
          role="presentation"
        >
          {lesson.media_type === 'video' ? (
            <video {...mediaProps} playsInline className="ef-media" />
          ) : (
            <audio {...mediaProps} className="ef-media" />
          )}

          {(displayMode === 'audio' || lesson.media_type !== 'video') && (
            <div className="ef-stage-art" aria-hidden>
              <IconHeadphones />
            </div>
          )}

          {captionOn && currentCue && (
            <div className="ef-caption" aria-live="polite" data-mode={displayMode}>
              {currentCue.text}
            </div>
          )}

          {stalled && (
            <div className="ef-stage-note">当前设备无法解码（AV1 / Opus），可换其它课时</div>
          )}

          {controlsOn && (
            <div className="ef-ovl" onClick={(event) => event.stopPropagation()}>
              <input
                className="ef-seek"
                type="range"
                min={0}
                max={Math.max(1, duration)}
                step={0.5}
                value={position}
                aria-label="播放进度"
                onChange={(event) => jumpTo(Number(event.target.value))}
              />
              <div className="ef-times">
                <span>{fmtTime(position)}</span>
                <span>-{fmtTime(Math.max(0, duration - position))}</span>
              </div>

              <div className="ef-ovl-row">
                <button type="button" className="ef-ctl" onClick={() => skip(-15)} aria-label="后退 15 秒">
                  <IconBack15 />
                </button>
                <button
                  type="button"
                  className="ef-ctl ef-ctl--play"
                  onClick={togglePlay}
                  aria-label={playing ? '暂停' : '播放'}
                >
                  {playing ? <IconPause /> : <IconPlay />}
                </button>
                <button type="button" className="ef-ctl" onClick={() => skip(15)} aria-label="前进 15 秒">
                  <IconForward15 />
                </button>
              </div>

              <div className="ef-ovl-row ef-ovl-row--tools">
                <button
                  type="button"
                  className="ef-ctl"
                  data-on={captionOn}
                  onClick={() => setCaptionOn((value) => !value)}
                  aria-label="画面字幕"
                  title="画面字幕"
                >
                  <IconCaptions />
                </button>
                <button
                  type="button"
                  className="ef-ctl"
                  data-on={listOn}
                  onClick={() => setListOn((value) => !value)}
                  aria-label="字幕列表"
                  title="字幕列表"
                >
                  <IconList />
                </button>
                <button type="button" className="ef-ctl" onClick={cycleSpeed} aria-label="倍速" title="倍速">
                  <IconSpeed />
                  <span className="ef-ctl-badge">{speed}x</span>
                </button>
                <button
                  type="button"
                  className="ef-ctl"
                  data-on={displayMode === 'float'}
                  onClick={() => setDisplayMode(displayMode === 'float' ? 'normal' : 'float')}
                  aria-label="悬浮窗口"
                  title="悬浮窗口"
                >
                  <IconFloat />
                </button>
                <button
                  type="button"
                  className="ef-ctl"
                  data-on={displayMode === 'audio'}
                  onClick={() => setDisplayMode(displayMode === 'audio' ? 'normal' : 'audio')}
                  aria-label="音频模式"
                  title="音频模式"
                >
                  <IconHeadphones />
                </button>
                <button
                  type="button"
                  className="ef-ctl"
                  data-on={displayMode === 'max'}
                  onClick={() => setDisplayMode(displayMode === 'max' ? 'normal' : 'max')}
                  aria-label="全屏"
                  title="全屏"
                >
                  {displayMode === 'max' ? <IconShrink /> : <IconExpand />}
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="ef-repeat-control">
          <label htmlFor="ef-repeat-target">本课完整听完</label>
          <select
            id="ef-repeat-target"
            value={repeatTarget}
            onChange={(event) => {
              const target = Number(event.target.value);
              setRepeatTarget(target);
              report({ repeatTarget: target });
            }}
          >
            {[1, 2, 3, 5, 10].map((count) => <option key={count} value={count}>{count} 遍</option>)}
          </select>
          <span>{completedPlays}/{repeatTarget}</span>
        </div>

        {listOn && displayMode !== 'max' ? (
          <div className="ef-subtitle-area">
            <div className="ef-subs" ref={subsRef} onScroll={onSubtitleScroll}>
              {groupedCues.length === 0 ? (
                <p style={{ color: 'rgba(248,250,252,0.6)', fontSize: 14 }}>
                  这一课没有字幕。仍然可以收听，但无法逐句跟读。
                </p>
              ) : (
                groupedCues.map(({ cue, showSpeaker }, index) => (
                  <div key={cue.i}>
                    {showSpeaker && (
                      <div className="ef-speaker" data-spk={cue.spk}>
                        <span className="ef-spk-badge">{cue.spk}</span>
                      </div>
                    )}
                    <div
                      role="button"
                      tabIndex={0}
                      ref={(node) => {
                        cueRefs.current[index] = node;
                      }}
                      className="ef-cue"
                      data-active={index === active}
                      onClick={() => selectCue(cue, index)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') selectCue(cue, index);
                      }}
                    >
                    {/* Every word is tappable: "查词" used to guess one word
                        for the sentence, so the learner could not look up the
                        word they actually did not know. */}
                    {cue.text.split(/(\s+)/).map((part, partIndex) =>
                      /[A-Za-z]/.test(part) ? (
                        <span
                          key={`${cue.i}-w${partIndex}`}
                          className="ef-word-tap"
                          role="button"
                          tabIndex={0}
                          onClick={(event) => {
                            event.stopPropagation();
                            setWordSheet({ word: part.replace(/[^A-Za-z'’\-]/g, ''), sentence: cue.text });
                          }}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') {
                              event.stopPropagation();
                              setWordSheet({ word: part.replace(/[^A-Za-z'’\-]/g, ''), sentence: cue.text });
                            }
                          }}
                        >
                          {part}
                        </span>
                      ) : (
                        <span key={`${cue.i}-s${partIndex}`}>{part}</span>
                      ),
                    )}
                    </div>
                  </div>
                ))
              )}
            </div>
            {showReturnTop && (
              <button
                type="button"
                className="ef-return-top"
                onClick={() => {
                  subsRef.current?.scrollTo({ top: 0, behavior: 'smooth' });
                  setShowReturnTop(false);
                  setFollow(false);
                }}
              >
                <ArrowUpOutlined /> 返回顶部
              </button>
            )}
          </div>
        ) : (
          <div style={{ flex: 1 }} />
        )}

        <div style={{ height: 8 }} />
      </div>

      {wordSheet && (
        <WordSheet
          key={`${wordSheet.word}:${wordSheet.sentence}`}
          word={wordSheet.word}
          sentence={wordSheet.sentence}
          lessonId={lesson.id}
          onClose={() => setWordSheet(null)}
        />
      )}

    </div>
  );
}

export function WordSheet({
  word,
  sentence,
  lessonId,
  onClose,
}: {
  word: string;
  sentence: string;
  lessonId?: number;
  onClose: () => void;
}) {
  const [selectedWord, setSelectedWord] = useState(word);
  const [entry, setEntry] = useState<WordEntry | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [added, setAdded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    setEntry(null);
    setAdded(hasWord(selectedWord));
    apiPost
      .word(selectedWord, sentence, lessonId)
      .then((data) => {
        if (!cancelled) setEntry(data);
      })
      .catch((err) => {
        if (!cancelled) setError(errText(err, '暂时无法查到该词'));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedWord, sentence, lessonId]);

  const highlighted = useMemo(() => {
    const lower = selectedWord.toLowerCase();
    const parts = sentence.split(/([A-Za-z]+(?:['’\-][A-Za-z]+)*)/);
    return parts.map((part, index) =>
      /^[A-Za-z]/.test(part) ? (
        <button
          key={index}
          type="button"
          className="ef-sheet-word"
          aria-pressed={part.toLowerCase() === lower}
          onClick={() => setSelectedWord(part)}
        >
          {part}
        </button>
      ) : <span key={index}>{part}</span>,
    );
  }, [sentence, selectedWord]);

  return (
    <div className="ef-sheet-mask" onClick={onClose} role="presentation">
      <div className="ef-sheet" onClick={(event) => event.stopPropagation()}>
        <div className="ef-sheet-grip" />
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <div>
            <h3 className="ef-word">{selectedWord}</h3>
            {entry?.phonetic && <p className="ef-phonetic">{entry.phonetic}</p>}
          </div>
          <button type="button" className="ef-icon-btn" onClick={onClose} aria-label="关闭">
            <IconClose />
          </button>
        </div>

        {loading && <div className="ef-skel" style={{ height: 64, marginTop: 16 }} />}

        {!loading && error && (
          <div className="ef-notice" style={{ marginTop: 16 }}>
            {error}
          </div>
        )}

        {!loading &&
          entry?.senses.map((sense, index) => (
            <div key={index} className="ef-sense">
              <span className="ef-pos">{sense.pos}</span>
              <span>{sense.meaning}</span>
            </div>
          ))}

        {!loading && entry?.context_meaning && (
          <div className="ef-context-meaning">
            <strong>本句含义</strong>
            <p>{entry.context_meaning}</p>
          </div>
        )}

        {!loading && entry?.phrases?.map((phrase) => (
          <div className="ef-context-phrase" key={phrase.phrase}>
            <strong>{phrase.phrase}</strong>
            <p>{phrase.meaning}</p>
            {phrase.explanation && <p>{phrase.explanation}</p>}
          </div>
        ))}

        {!loading && entry?.context_status === 'unavailable' && (
          <p className="ef-context-status">语境分析暂不可用，当前显示基础释义。</p>
        )}

        {!loading && entry?.context_status === 'complete' && entry.phrases?.length === 0 && (
          <p className="ef-context-status">本句未识别到包含该词的固定短语或搭配。</p>
        )}

        {!loading && (entry?.examples?.length || entry?.example) ? (
          <div className="ef-word-examples">
            <strong>例句</strong>
            {(entry.examples?.length ? entry.examples : [entry.example]).filter(Boolean).slice(0, 2).map((example, index) => (
              <p key={index}>{example}</p>
            ))}
          </div>
        ) : null}

        {sentence && <div className="ef-word-original"><strong>原文句子</strong><p className="ef-quote">{highlighted}</p></div>}

        <div className="ef-btn-row">
          <button
            type="button"
            className="ef-btn"
            data-kind={added ? 'ghost' : 'primary'}
            disabled={loading || Boolean(error) || added}
            onClick={() => {
              if (!entry || hasWord(selectedWord)) {
                setAdded(true);
                return;
              }
              saveWord({
                word: selectedWord,
                phonetic: entry.phonetic,
                meaning: entry.context_meaning || entry.senses[0]?.meaning || '',
                examples: (entry.examples?.length ? entry.examples : [entry.example]).filter(Boolean).slice(0, 2),
                lessonId,
                sentence,
              });
              setAdded(true);
            }}
          >
            {added ? '已添加 ✓' : '加入生词'}
          </button>
          <button
            type="button"
            className="ef-btn"
            onClick={() => {
              // Falls back to the device voice when no pronunciation audio
              // exists: zero backend cost and works offline.
              if ('speechSynthesis' in window) {
                const utterance = new SpeechSynthesisUtterance(selectedWord);
                utterance.lang = 'en-US';
                window.speechSynthesis.speak(utterance);
              }
            }}
          >
            朗读
          </button>
        </div>
      </div>
    </div>
  );
}
