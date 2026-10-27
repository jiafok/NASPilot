import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  apiGet,
  apiPost,
  errText,
  fmtTime,
  withToken,
  type LessonDetail,
  type Segment,
} from './api';
import { IconBack, IconClose, IconPause, IconPlay, IconStar } from './components';

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
  onClose: () => void;
  /** Continue to another lesson without leaving the player. */
  onOpenLesson: (lessonId: number) => void;
}

const SPEEDS = [0.75, 1, 1.25, 1.5];
type SubMode = 'en' | 'hidden';

export default function PlayerPage({ lessonId, onClose, onOpenLesson }: Props) {
  const [lesson, setLesson] = useState<LessonDetail | null>(null);
  const [cues, setCues] = useState<Segment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [active, setActive] = useState(-1);
  const [speed, setSpeed] = useState(1);
  const [subMode, setSubMode] = useState<SubMode>('en');
  const [wordSheet, setWordSheet] = useState<{ word: string; sentence: string } | null>(null);
  const [follow, setFollow] = useState(true);
  const [stalled, setStalled] = useState(false);

  const mediaRef = useRef<HTMLVideoElement | HTMLAudioElement | null>(null);
  const subsRef = useRef<HTMLDivElement | null>(null);
  const syncRef = useRef<SubtitleSync | null>(null);
  const cueRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const reportedRef = useRef(0);
  const listenedRef = useRef(0);
  const lastTickRef = useRef(0);
  const followTimerRef = useRef<number | null>(null);
  const autoScrollAtRef = useRef(0);

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
    (options: { completed?: boolean } = {}) => {
      if (!lesson) return;
      const media = mediaRef.current;
      const current = media?.currentTime ?? position;
      const delta = Math.max(0, Math.round(listenedRef.current));
      if (!delta && options.completed === undefined) return;
      listenedRef.current = 0;
      void apiPost
        .progress({
          lesson_id: lesson.id,
          position: Math.round(current * 10) / 10,
          listen_seconds: delta,
          ...(options.completed === undefined ? {} : { completed: options.completed }),
        })
        .catch(() => {
          // Never interrupt playback for a failed heartbeat.
          listenedRef.current += delta;
        });
    },
    [lesson, position],
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
    const node = cueRefs.current[active];
    const container = subsRef.current;
    if (!node || !container) return;
    const target = node.offsetTop - container.clientHeight / 2 + node.clientHeight / 2;
    // Mark the moment we scroll ourselves: the resulting `scroll` event would
    // otherwise be mistaken for the user taking over and would switch follow
    // off after the very first line.
    autoScrollAtRef.current = Date.now();
    container.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
  }, [active, follow]);

  const pauseFollow = useCallback(() => {
    // Ignore the scroll events our own animation produces.
    if (Date.now() - autoScrollAtRef.current < 700) return;
    setFollow(false);
    if (followTimerRef.current) window.clearTimeout(followTimerRef.current);
    followTimerRef.current = window.setTimeout(() => setFollow(true), 3000);
  }, []);

  const seekTo = useCallback((cue: Segment) => {
    const media = mediaRef.current;
    if (!media) return;
    media.currentTime = cue.start / 1000;
    setFollow(true);
    void media.play().catch(() => undefined);
  }, []);

  const togglePlay = useCallback(() => {
    const media = mediaRef.current;
    if (!media) return;
    if (media.paused) void media.play().catch(() => undefined);
    else media.pause();
  }, []);

  const skip = useCallback((seconds: number) => {
    const media = mediaRef.current;
    if (!media) return;
    media.currentTime = Math.max(0, Math.min(media.duration || duration, media.currentTime + seconds));
  }, [duration]);

  const cycleSpeed = useCallback(() => {
    const index = SPEEDS.indexOf(speed);
    const next = SPEEDS[(index + 1) % SPEEDS.length];
    setSpeed(next);
    if (mediaRef.current) mediaRef.current.playbackRate = next;
  }, [speed]);

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

  if (loading) {
    return (
      <div className="ef-player">
        <div className="ef-player-inner">
          <div className="ef-player-top">
            <button type="button" className="ef-icon-btn" onClick={onClose} aria-label="返回">
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
      <div className="ef-player">
        <div className="ef-player-inner">
          <div className="ef-player-top">
            <button type="button" className="ef-icon-btn" onClick={onClose} aria-label="返回">
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
    <div className="ef-player">
      <div
        className="ef-player-bg"
        style={{ backgroundImage: `url(${withToken(lesson.cover)})` }}
        aria-hidden
      />
      <div className="ef-player-veil" aria-hidden />

      <div className="ef-player-inner">
        <div className="ef-player-top">
          <button type="button" className="ef-icon-btn" onClick={onClose} aria-label="返回">
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

        {/* Hidden media element: the video itself is not the point here — the
            learner listens and reads. Video still plays so lip movement helps. */}
        {lesson.media_type === 'video' ? (
          <video
            {...mediaProps}
            playsInline
            style={{ width: '100%', maxHeight: 180, borderRadius: 12, background: '#000' }}
          />
        ) : (
          <audio {...mediaProps} style={{ display: 'none' }} />
        )}

        <div className="ef-player-controls">
          <input
            className="ef-seek"
            type="range"
            min={0}
            max={Math.max(1, duration)}
            step={0.5}
            value={position}
            aria-label="播放进度"
            onChange={(event) => {
              const next = Number(event.target.value);
              setPosition(next);
              if (mediaRef.current) mediaRef.current.currentTime = next;
            }}
          />
          <div className="ef-times">
            <span>{fmtTime(position)}</span>
            <span>-{fmtTime(Math.max(0, duration - position))}</span>
          </div>

          <div className="ef-control-row">
            <button
              type="button"
              className="ef-skip"
              onClick={() => skip(-15)}
              aria-label="后退 15 秒"
            >
              ↺15
            </button>
            <button
              type="button"
              className="ef-play-btn"
              onClick={togglePlay}
              aria-label={playing ? '暂停' : '播放'}
            >
              {playing ? <IconPause /> : <IconPlay />}
            </button>
            <button
              type="button"
              className="ef-skip"
              onClick={() => skip(15)}
              aria-label="前进 15 秒"
            >
              15↻
            </button>
          </div>

          <div className="ef-sub-controls">
            <button type="button" className="ef-sub-btn" onClick={cycleSpeed}>
              <span style={{ fontSize: 14, fontWeight: 600 }}>{speed}x</span>
              <span>倍速</span>
            </button>
            <button
              type="button"
              className="ef-sub-btn"
              data-on={subMode === 'en'}
              onClick={() => setSubMode(subMode === 'en' ? 'hidden' : 'en')}
            >
              <span style={{ fontSize: 14, fontWeight: 600 }}>CC</span>
              <span>{subMode === 'en' ? '字幕开' : '字幕关'}</span>
            </button>
            <button
              type="button"
              className="ef-sub-btn"
              data-on={follow}
              onClick={() => setFollow((value) => !value)}
            >
              <span style={{ fontSize: 14, fontWeight: 600 }}>{follow ? '跟随' : '自由'}</span>
              <span>滚动</span>
            </button>
          </div>
        </div>

        {subMode === 'en' ? (
          <div
            className="ef-subs"
            ref={subsRef}
            onScroll={pauseFollow}
            onTouchStart={pauseFollow}
            onWheel={pauseFollow}
          >
            {groupedCues.length === 0 ? (
              <p style={{ color: 'rgba(248,250,252,0.6)', fontSize: 14 }}>
                这一课没有字幕。仍然可以收听，但无法逐句跟读。
              </p>
            ) : (
              groupedCues.map(({ cue, showSpeaker }, index) => (
                <div key={cue.i}>
                  {/* A podcast alternates speakers almost every line, so a
                      full "说话人 A" label per turn is pure noise. A tinted
                      monogram carries the same grouping in 16px. */}
                  {showSpeaker && (
                    <div className="ef-speaker" data-spk={cue.spk}>
                      <span className="ef-spk-badge">{cue.spk}</span>
                    </div>
                  )}
                  <button
                    type="button"
                    ref={(node) => {
                      cueRefs.current[index] = node;
                    }}
                    className="ef-cue"
                    data-active={index === active}
                    onClick={() => seekTo(cue)}
                  >
                    {cue.text}
                  </button>
                </div>
              ))
            )}
          </div>
        ) : (
          <div style={{ flex: 1 }} />
        )}

        {stalled && (
          <div
            className="ef-notice"
            style={{ background: 'rgba(245,158,11,0.18)', color: '#fbbf24', marginTop: 8 }}
          >
            这一课在当前设备上无法解码（部分下载源的视频为 AV1、音频为 Opus，iPhone 不支持）。
            可以试试其它课时，或让管理员把它转成 H.264/AAC。
          </div>
        )}

        {subMode === 'en' && currentCue && (
          <div
            style={{
              position: 'absolute',
              right: 20,
              // Sits above the prev/next bar, which would otherwise swallow taps.
              bottom: 68,
              display: 'flex',
              gap: 8,
            }}
          >
            <button
              type="button"
              className="ef-btn"
              style={{ background: 'rgba(255,255,255,0.12)', color: '#f8fafc', border: 0 }}
              onClick={() => {
                const text = currentCue.text;
                // Tapped sentence → offer the words that are worth looking up.
                const word = pickWord(text);
                if (word) setWordSheet({ word, sentence: text });
              }}
            >
              查词
            </button>
          </div>
        )}

        <div style={{ height: 8 }} />
      </div>

      {wordSheet && (
        <WordSheet
          word={wordSheet.word}
          sentence={wordSheet.sentence}
          lessonId={lesson.id}
          onClose={() => setWordSheet(null)}
        />
      )}

      {/* Next/previous keep the session going without a trip through the list. */}
      <div
        style={{
          position: 'absolute',
          inset: 'auto 0 0 0',
          display: 'flex',
          gap: 8,
          padding: '10px 20px calc(12px + env(safe-area-inset-bottom, 0px))',
          background: 'linear-gradient(to top, rgba(15,23,42,0.96), rgba(15,23,42,0))',
          pointerEvents: 'none',
        }}
      >
        {lesson.prev_lesson_id && (
          <button
            type="button"
            className="ef-btn"
            style={{ pointerEvents: 'auto', background: 'rgba(255,255,255,0.1)', color: '#f8fafc', border: 0 }}
            onClick={() => lesson.prev_lesson_id && onOpenLesson(lesson.prev_lesson_id)}
          >
            上一课
          </button>
        )}
        {lesson.next_lesson_id && (
          <button
            type="button"
            className="ef-btn"
            style={{ pointerEvents: 'auto', marginLeft: 'auto', background: 'rgba(255,255,255,0.1)', color: '#f8fafc', border: 0 }}
            onClick={() => lesson.next_lesson_id && onOpenLesson(lesson.next_lesson_id)}
          >
            下一课
          </button>
        )}
      </div>
    </div>
  );
}

/** Longest alphabetic token: usually the most informative word in a sentence. */
function pickWord(text: string): string | null {
  const words = text.match(/[A-Za-z][A-Za-z'-]{2,}/g);
  if (!words || words.length === 0) return null;
  return words.reduce((best, word) => (word.length > best.length ? word : best), words[0]);
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
  const [entry, setEntry] = useState<{
    phonetic: string;
    senses: Array<{ pos: string; meaning: string }>;
    example: string;
  } | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [added, setAdded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    apiPost
      .word(word, sentence, lessonId)
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
  }, [word, sentence, lessonId]);

  const highlighted = useMemo(() => {
    const lower = word.toLowerCase();
    const parts = sentence.split(/(\b[A-Za-z'-]+\b)/);
    return parts.map((part, index) =>
      part.toLowerCase() === lower ? <em key={index}>{part}</em> : <span key={index}>{part}</span>,
    );
  }, [sentence, word]);

  return (
    <div className="ef-sheet-mask" onClick={onClose} role="presentation">
      <div className="ef-sheet" onClick={(event) => event.stopPropagation()}>
        <div className="ef-sheet-grip" />
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
          <div>
            <h3 className="ef-word">{word}</h3>
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

        {!loading && entry?.example && (
          <p className="ef-quote" style={{ marginTop: 16 }}>
            {entry.example}
          </p>
        )}

        <p className="ef-quote">{highlighted}</p>

        <div className="ef-btn-row">
          <button
            type="button"
            className="ef-btn"
            data-kind={added ? 'ghost' : 'primary'}
            disabled={loading || Boolean(error)}
            onClick={() => setAdded(true)}
          >
            {added ? '已加入生词 ✓' : '加入生词'}
          </button>
          <button
            type="button"
            className="ef-btn"
            onClick={() => {
              // Falls back to the device voice when no pronunciation audio
              // exists: zero backend cost and works offline.
              if ('speechSynthesis' in window) {
                const utterance = new SpeechSynthesisUtterance(word);
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
