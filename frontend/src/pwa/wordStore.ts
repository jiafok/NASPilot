/** Locally saved words.
 *
 * Stage-0 shipped `/english/word` (lookup) but no server-side word book, so
 * saves live on the device. That is stated in the UI rather than implied to be
 * synced — a button that silently drops the data would be worse than no button.
 */

export interface SavedWord {
  word: string;
  phonetic: string;
  meaning: string;
  lessonId?: number;
  sentence?: string;
  addedAt: number;
}

const KEY = 'ef_words';

export function listWords(): SavedWord[] {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? (JSON.parse(raw) as SavedWord[]) : [];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function saveWord(entry: Omit<SavedWord, 'addedAt'>): SavedWord[] {
  const existing = listWords();
  const key = entry.word.toLowerCase();
  if (existing.some((item) => item.word.toLowerCase() === key)) return existing;
  const next = [{ ...entry, addedAt: Date.now() }, ...existing].slice(0, 500);
  localStorage.setItem(KEY, JSON.stringify(next));
  return next;
}

export function removeWord(word: string): SavedWord[] {
  const next = listWords().filter((item) => item.word.toLowerCase() !== word.toLowerCase());
  localStorage.setItem(KEY, JSON.stringify(next));
  return next;
}

export function hasWord(word: string): boolean {
  return listWords().some((item) => item.word.toLowerCase() === word.toLowerCase());
}
