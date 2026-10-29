/** Typed client for the English Learning API, plus shared view models.
 *
 * The PWA talks to the same endpoints as the desktop page. Keeping the shapes
 * in one place means a backend field rename shows up as a TypeScript error
 * instead of a silently empty screen.
 */

import api from '../utils/api';

export interface Course {
  id: number;
  title: string;
  channel: string;
  category: string;
  level: string;
  topics: string[];
  skills: string[];
  format: string;
  description?: string | null;
  lesson_count: number;
  total_seconds: number;
  cover?: string | null;
  is_favorite: boolean;
}

export interface LessonBrief {
  id: number;
  title: string;
  position: number;
  duration: number;
  media_type: string;
  has_subtitle: boolean;
  cover?: string | null;
  progress: number;
  completed: boolean;
  level: string;
  topics: string[];
  skills: string[];
  format: string;
}

export interface CourseDetail extends Course {
  lessons: LessonBrief[];
  vocabulary_count: number;
}

export interface Segment {
  i: number;
  /** Milliseconds. */
  start: number;
  end: number;
  text: string;
  /** "A"/"B" when the source marks a speaker change; null for monologue. */
  spk?: string | null;
}

export interface LessonDetail {
  id: number;
  course_id: number;
  title: string;
  media_type: string;
  duration: number;
  stream_url: string;
  subtitle_url?: string | null;
  has_subtitle: boolean;
  segment_count: number;
  level: string;
  topics: string[];
  skills: string[];
  format: string;
  cover: string;
  progress: { position: number; completed: boolean; listen_seconds: number; repeat_target: number; completed_plays: number };
  prev_lesson_id?: number | null;
  next_lesson_id?: number | null;
}

export interface HomeData {
  greeting: string;
  user: { id: number; name: string };
  stats: {
    courses: number;
    lessons: number;
    completed: number;
    minutes_today: number;
    total_time: string;
  };
  continue_learning: Array<{
    lesson_id: number;
    lesson_title: string;
    course_id: number;
    course_title: string;
    channel: string;
    cover?: string | null;
    duration: number;
    position: number;
    progress: number;
    completed_plays: number;
    repeat_target: number;
  }>;
  recent_completed: Array<{
    lesson_id: number;
    lesson_title: string;
    course_id: number;
    course_title: string;
    cover?: string | null;
    duration: number;
    completed_plays: number;
    repeat_target: number;
    last_played_at: string | null;
  }>;
  recent: Course[];
  recommended: Course[];
}

export interface Filters {
  channels?: string[];
  levels?: Array<{ value: string; count: number }> | string[];
  topics?: Array<{ value: string; count: number }> | string[];
  skills?: Array<{ value: string; count: number }> | string[];
  formats?: Array<{ value: string; count: number }> | string[];
  lesson_filters?: Record<string, unknown>;
}

export interface StatsPayload {
  days: number;
  streak: number;
  today: { day: string; seconds: number; minutes: number };
  series: Array<{ day: string; seconds: number; minutes: number; lessons_completed: number }>;
  week: { seconds: number; minutes: number; days_active: number };
  month: { seconds: number; minutes: number; days_active: number };
  totals: {
    lessons_started: number;
    lessons_completed: number;
    listen_seconds: number;
    listen_minutes: number;
    favorites: number;
    bookmarks: number;
    active_days: number;
  };
  levels: Record<string, number>;
  last_played_at: string | null;
}

export interface WordEntry {
  word: string;
  phonetic: string;
  senses: Array<{ pos: string; meaning: string }>;
  example: string;
  examples?: string[];
  source: string;
  cached?: boolean;
  context_meaning?: string;
  context_status?: 'complete' | 'unavailable';
  phrases?: Array<{ phrase: string; meaning: string; explanation: string }>;
}

/** Normalise the two possible filter shapes into `{value, count}` pairs. */
export function filterOptions(
  value: Filters[keyof Filters] | undefined,
): Array<{ value: string; count?: number }> {
  if (!Array.isArray(value)) return [];
  return value.map((item) =>
    typeof item === 'string' ? { value: item } : (item as { value: string; count?: number }),
  );
}

export const apiGet = {
  home: () => api.get<HomeData>('/english/home').then((r) => r.data),
  courses: (params?: Record<string, unknown>) =>
    api.get<{ items: Course[]; total?: number; page?: number }>('/english/courses', { params }).then((r) => r.data),
  lessons: (params?: Record<string, unknown>) =>
    api
      .get<{ items: Array<LessonBrief & { course_title: string; course_id: number }> }>('/english/lessons', {
        params,
      })
      .then((r) => r.data),
  filters: () => api.get<Filters>('/english/filters').then((r) => r.data),
  course: (id: number) => api.get<CourseDetail>(`/english/course/${id}`).then((r) => r.data),
  lesson: (id: number) => api.get<LessonDetail>(`/english/lesson/${id}`).then((r) => r.data),
  transcript: (id: number) =>
    api.get<{ segments: Segment[] }>(`/english/transcript/${id}`).then((r) => r.data),
  stats: (days = 90) => api.get<StatsPayload>('/english/stats', { params: { days } }).then((r) => r.data),
  aiStatus: () =>
    api
      .get<{ configured: boolean; model?: string }>('/english/ai/status')
      .then((r) => r.data)
      .catch(() => ({ configured: false })),
};

export const apiPost = {
  progress: (payload: {
    lesson_id: number;
    position: number;
    listen_seconds?: number;
    completed?: boolean;
    repeat_target?: number;
  }) => api.post('/english/progress', payload),
  favorite: (course_id: number, favorite: boolean) =>
    api.post('/english/favorite', { course_id, favorite }),
  word: (word: string, sentence?: string, lesson_id?: number) =>
    api.post<WordEntry>('/english/word', { word, sentence, lesson_id }, { timeout: 90000 }).then((r) => r.data),
};

/** Format seconds as `m:ss` (or `h:mm:ss` past an hour). */
export function fmtTime(seconds: number | null | undefined): string {
  const total = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

/**
 * Stream URLs need the token in the query string: `<audio src>` cannot carry an
 * Authorization header, so the backend accepts `?token=` for these routes only.
 */
export function withToken(path: string | null | undefined): string | undefined {
  if (!path) return undefined;
  const token = localStorage.getItem('token') || '';
  if (!token) return path;
  return `${path}${path.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

export const LEVEL_LABEL: Record<string, string> = {
  A1: '入门',
  A2: '基础',
  B1: '进阶',
  B2: '高阶',
  C1: '精通',
};

export const FORMAT_LABEL: Record<string, string> = {
  dialogue: '对话',
  interview: '访谈',
  monologue: '独白',
  lecture: '讲座',
  news: '新闻',
  story: '故事',
  song: '歌曲',
};

/** Pull a human-readable message out of an API error. */
export function errText(err: unknown, fallback: string): string {
  const anyErr = err as { response?: { data?: { detail?: unknown } } };
  const detail = anyErr?.response?.data?.detail;
  if (typeof detail === 'string' && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return String((detail[0] as { msg?: string }).msg || fallback);
  }
  return fallback;
}
