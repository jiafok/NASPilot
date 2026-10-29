/** The learner's saved words (生词本) — now stored on the server.
 *
 * Stage-0 kept this list in `localStorage['ef_words']`, so the same account
 * showed a different word book on every device and nothing to sync. The list
 * now lives in the `saved_words` table behind `/english/words`.
 *
 * Three things this module still does locally, and why:
 *
 * * **`hasWord()` stays synchronous.** The word card renders "已添加 ✓" on the
 *   first paint, and turning that into a network round-trip would make the
 *   button flicker from "加入生词" to "已添加" on every open. It reads a memory
 *   index that is hydrated from the last server response (mirrored into
 *   `localStorage`) and updated optimistically after each save.
 * * **Legacy migration.** Anyone who used the old build has words sitting in
 *   `ef_words`. The first successful list call uploads them once — keeping
 *   their original `addedAt` — and only clears the key when every word landed.
 * * **Offline mirror.** If the server is unreachable the list renders from the
 *   cache instead of going blank; the next successful call replaces it.
 */

import api from '../utils/api';

export interface SavedWord {
  word: string;
  /** Normalised dedupe key (lowercased). Absent on legacy local entries. */
  key?: string;
  phonetic: string;
  meaning: string;
  lessonId?: number | null;
  sentence?: string | null;
  examples?: string[];
  addedAt: number;
}

/** Old, device-only storage — read once for migration, never written again. */
const LEGACY_KEY = 'ef_words';
/** Offline mirror of the server list. */
const CACHE_KEY = 'ef_words_cache';

const keyOf = (word: string | null | undefined): string =>
  (word || '').trim().toLowerCase();

function readJson<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function readCache(): SavedWord[] {
  const parsed = readJson<SavedWord[]>(CACHE_KEY);
  return Array.isArray(parsed) ? parsed : [];
}

function writeCache(items: SavedWord[]): void {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(items.slice(0, 2000)));
  } catch {
    // Storage full / private mode — the server list still works.
  }
}

/** Memory index so `hasWord()` can answer without a request. */
let index = new Set<string>(readCache().map((item) => keyOf(item.key || item.word)));

function rebuildIndex(items: SavedWord[]): void {
  index = new Set(items.map((item) => keyOf(item.key || item.word)));
}

/** True when the word is already in the book, using the last known state. */
export function hasWord(word: string): boolean {
  return index.has(keyOf(word));
}

export function cachedWords(): SavedWord[] {
  return readCache();
}

let migration: Promise<void> | null = null;

/** Upload whatever the old build left in `localStorage`. Runs at most once. */
function migrateLegacyWords(): Promise<void> {
  migration = migration ?? (async () => {
    const legacy = readJson<SavedWord[]>(LEGACY_KEY);
    if (!Array.isArray(legacy) || legacy.length === 0) {
      localStorage.removeItem(LEGACY_KEY);
      return;
    }
    const failed: SavedWord[] = [];
    for (const item of legacy) {
      if (!item?.word) continue;
      try {
        await api.post('/english/words', {
          word: item.word,
          phonetic: item.phonetic || null,
          meaning: item.meaning || null,
          lesson_id: item.lessonId ?? null,
          sentence: item.sentence || null,
          examples: item.examples ?? [],
          added_at: item.addedAt || undefined,
        });
      } catch {
        failed.push(item);
      }
    }
    if (failed.length === 0) localStorage.removeItem(LEGACY_KEY);
    else localStorage.setItem(LEGACY_KEY, JSON.stringify(failed));
  })();
  return migration;
}

/** The word book, newest first. Falls back to the offline mirror on failure. */
export async function listWords(): Promise<SavedWord[]> {
  await migrateLegacyWords();
  try {
    const { data } = await api.get<{ items: SavedWord[] }>('/english/words');
    const items = Array.isArray(data?.items) ? data.items : [];
    writeCache(items);
    rebuildIndex(items);
    return items;
  } catch {
    return readCache();
  }
}

/** Save one word. Idempotent server-side, so a retry is always safe. */
export async function saveWord(
  entry: Omit<SavedWord, 'addedAt'> & { addedAt?: number },
): Promise<SavedWord> {
  const { data } = await api.post<SavedWord>('/english/words', {
    word: entry.word,
    phonetic: entry.phonetic || null,
    meaning: entry.meaning || null,
    lesson_id: entry.lessonId ?? null,
    sentence: entry.sentence || null,
    examples: entry.examples ?? [],
    added_at: entry.addedAt,
  });

  const key = keyOf(data?.key || entry.word);
  index.add(key);
  const next = [data, ...readCache().filter((item) => keyOf(item.key || item.word) !== key)];
  writeCache(next);
  return data;
}

/** Delete one word from this account's book. Returns the remaining list. */
export async function removeWord(word: string): Promise<SavedWord[]> {
  await api.delete(`/english/words/${encodeURIComponent(word)}`);
  const key = keyOf(word);
  index.delete(key);
  const next = readCache().filter((item) => keyOf(item.key || item.word) !== key);
  writeCache(next);
  return next;
}
