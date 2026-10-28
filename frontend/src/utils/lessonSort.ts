export const LESSON_SORTS = [
  { value: 'position', label: '课程顺序' },
  { value: 'title', label: '名称 A–Z' },
  { value: 'duration-asc', label: '时长：短到长' },
  { value: 'duration-desc', label: '时长：长到短' },
  { value: 'level', label: '难度：低到高' },
] as const;

export type LessonSort = typeof LESSON_SORTS[number]['value'];

export function sortLessons<T extends { id: number; title: string; position: number; duration: number; level: string }>(
  lessons: T[], sort: LessonSort,
): T[] {
  const levels = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2'];
  const rank = (level: string) => levels.includes(level) ? levels.indexOf(level) : levels.length;
  return [...lessons].sort((first, second) => {
    let order = 0;
    if (sort === 'position') order = first.position - second.position;
    if (sort === 'title') order = first.title.localeCompare(second.title, 'en', { numeric: true });
    if (sort === 'duration-asc') order = first.duration - second.duration;
    if (sort === 'duration-desc') order = second.duration - first.duration;
    if (sort === 'level') order = rank(first.level) - rank(second.level);
    return order || first.position - second.position || first.id - second.id;
  });
}