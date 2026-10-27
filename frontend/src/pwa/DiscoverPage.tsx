import { useCallback, useEffect, useRef, useState } from 'react';
import { apiGet, errText, filterOptions, type Course, type Filters } from './api';
import { EmptyState, ErrorState, IconClose, IconSearch, SkeletonList } from './components';
import { CourseRow } from './HomePage';

interface Props {
  onOpenCourse: (courseId: number) => void;
}

const EMPTY: Filters = {};

export default function DiscoverPage({ onOpenCourse }: Props) {
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [courses, setCourses] = useState<Course[]>([]);
  const [query, setQuery] = useState('');
  const [level, setLevel] = useState<string | undefined>();
  const [topic, setTopic] = useState<string | undefined>();
  const [skill, setSkill] = useState<string | undefined>();
  const [format, setFormat] = useState<string | undefined>();
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const debounceRef = useRef<number | null>(null);
  const [debouncedQuery, setDebouncedQuery] = useState('');

  useEffect(() => {
    apiGet.filters().then(setFilters).catch(() => setFilters(EMPTY));
  }, []);

  // 300ms debounce: typing must not fire one request per keystroke.
  useEffect(() => {
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => setDebouncedQuery(query.trim()), 300);
    return () => {
      if (debounceRef.current) window.clearTimeout(debounceRef.current);
    };
  }, [query]);

  const fetchPage = useCallback(
    async (nextPage: number, append: boolean) => {
      setLoading(true);
      setError('');
      try {
        const params: Record<string, unknown> = { page: nextPage, page_size: 20 };
        if (debouncedQuery) params.q = debouncedQuery;
        if (level) params.level = level;
        if (topic) params.topic = topic;
        if (skill) params.skill = skill;
        if (format) params.format = format;
        const data = await apiGet.courses(params);
        const items = data.items || [];
        setCourses((prev) => (append ? [...prev, ...items] : items));
        setHasMore(items.length >= 20);
        setPage(nextPage);
      } catch (err) {
        setError(errText(err, '筛选失败，请稍后重试'));
        if (!append) setCourses([]);
      } finally {
        setLoading(false);
      }
    },
    [debouncedQuery, level, topic, skill, format],
  );

  useEffect(() => {
    void fetchPage(1, false);
  }, [fetchPage]);

  const activeFilters = [level, topic, skill, format].filter(Boolean) as string[];

  return (
    <div className="ef-page">
      <div className="ef-search">
        <IconSearch />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索课程、话题、讲师"
          inputMode="search"
          enterKeyHint="search"
        />
        {query && (
          <button type="button" className="ef-icon-btn" onClick={() => setQuery('')} aria-label="清除">
            <IconClose />
          </button>
        )}
      </div>

      <FilterChips
        label="难度"
        options={filterOptions(filters.levels)}
        value={level}
        onChange={setLevel}
      />
      <FilterChips
        label="话题"
        options={filterOptions(filters.topics)}
        value={topic}
        onChange={setTopic}
        limit={12}
      />
      <FilterChips
        label="技能"
        options={filterOptions(filters.skills)}
        value={skill}
        onChange={setSkill}
      />
      <FilterChips
        label="形式"
        options={filterOptions(filters.formats)}
        value={format}
        onChange={setFormat}
      />

      {activeFilters.length > 0 && (
        <div className="ef-chip-row">
          <button
            type="button"
            className="ef-chip"
            onClick={() => {
              setLevel(undefined);
              setTopic(undefined);
              setSkill(undefined);
              setFormat(undefined);
            }}
          >
            清除筛选 ×
          </button>
        </div>
      )}

      <section className="ef-section" style={{ marginTop: 12 }}>
        {loading && courses.length === 0 ? (
          <SkeletonList rows={5} />
        ) : error ? (
          <ErrorState message={error} onRetry={() => void fetchPage(1, false)} />
        ) : courses.length === 0 ? (
          <EmptyState
            title="没有符合条件的课程"
            description="试试放宽难度或换一个话题。"
            action="清除筛选"
            onAction={() => {
              setLevel(undefined);
              setTopic(undefined);
              setSkill(undefined);
              setFormat(undefined);
              setQuery('');
            }}
          />
        ) : (
          <>
            <div className="ef-list">
              {courses.map((course) => (
                <CourseRow
                  key={course.id}
                  course={course}
                  onOpen={() => onOpenCourse(course.id)}
                />
              ))}
            </div>
            {hasMore && (
              <button
                type="button"
                className="ef-btn"
                style={{ width: '100%', marginTop: 12 }}
                disabled={loading}
                onClick={() => void fetchPage(page + 1, true)}
              >
                {loading ? '加载中…' : '加载更多'}
              </button>
            )}
          </>
        )}
      </section>
    </div>
  );
}

function FilterChips({
  label,
  options,
  value,
  onChange,
  limit = 8,
}: {
  label: string;
  options: Array<{ value: string; count?: number }>;
  value: string | undefined;
  onChange: (next: string | undefined) => void;
  limit?: number;
}) {
  if (!options.length) return null;
  return (
    <div style={{ marginBottom: 4 }}>
      <div style={{ fontSize: 12, color: 'var(--ef-ink-sub)', marginBottom: 4 }}>{label}</div>
      <div className="ef-chip-row">
        {options.slice(0, limit).map((option) => (
          <button
            key={option.value}
            type="button"
            className="ef-chip"
            data-active={value === option.value}
            onClick={() => onChange(value === option.value ? undefined : option.value)}
          >
            {option.value}
            {option.count != null ? ` ${option.count}` : ''}
          </button>
        ))}
      </div>
    </div>
  );
}
