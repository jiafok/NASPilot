import { useCallback, useEffect, useState } from 'react';
import { apiGet, apiPost, errText, fmtTime, withToken, type CourseDetail, type LessonBrief } from './api';
import { EmptyState, ErrorState, IconBack, IconStar, SkeletonList } from './components';

interface Props {
  courseId: number;
  onBack: () => void;
  onOpenLesson: (lessonId: number) => void;
}

export default function CoursePage({ courseId, onBack, onOpenLesson }: Props) {
  const [course, setCourse] = useState<CourseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    apiGet
      .course(courseId)
      .then(setCourse)
      .catch((err) => setError(errText(err, '无法打开课程')))
      .finally(() => setLoading(false));
  }, [courseId]);

  useEffect(load, [load]);

  const toggleFavorite = async () => {
    if (!course) return;
    const next = !course.is_favorite;
    setCourse({ ...course, is_favorite: next });
    try {
      await apiPost.favorite(course.id, next);
    } catch {
      setCourse((prev) => (prev ? { ...prev, is_favorite: !next } : prev));
    }
  };

  const header = (
    <div className="ef-player-top" style={{ marginBottom: 8 }}>
      <button type="button" className="ef-icon-btn" onClick={onBack} aria-label="返回" style={{ color: 'inherit' }}>
        <IconBack />
      </button>
      <span />
    </div>
  );

  if (loading) {
    return (
      <div className="ef-page">
        {header}
        <SkeletonList rows={1} />
        <div style={{ height: 24 }} />
        <SkeletonList rows={6} />
      </div>
    );
  }

  if (error || !course) {
    return (
      <div className="ef-page">
        {header}
        <ErrorState message={error || '课程不存在'} onRetry={load} />
      </div>
    );
  }

  const finished = course.lessons.filter((lesson) => lesson.completed).length;

  return (
    <div className="ef-page">
      {header}

      <section className="ef-section">
        <div className="ef-continue" style={{ marginBottom: 12 }}>
          <span className="ef-cover" style={{ width: 104, height: 64 }}>
            {course.cover ? <img src={withToken(course.cover)} alt="" /> : null}
          </span>
          <div style={{ minWidth: 0 }}>
            <h1 className="ef-section-title" style={{ fontSize: 20 }}>
              {course.title}
            </h1>
            <p className="ef-sub" style={{ marginTop: 4 }}>
              {course.channel} · {course.lesson_count} 课 · {fmtTime(course.total_seconds)}
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
          <span className="ef-tag">{course.level}</span>
          {course.topics?.map((topic) => (
            <span key={topic} className="ef-tag" data-tone="plain">
              {topic}
            </span>
          ))}
          {course.skills?.map((skill) => (
            <span key={skill} className="ef-tag" data-tone="plain">
              {skill}
            </span>
          ))}
        </div>

        {course.description && <p className="ef-sub">{course.description}</p>}

        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <button
            type="button"
            className="ef-btn"
            data-kind={course.is_favorite ? 'primary' : undefined}
            onClick={() => void toggleFavorite()}
          >
            <IconStar filled={course.is_favorite} /> {course.is_favorite ? '已收藏' : '收藏'}
          </button>
          <button
            type="button"
            className="ef-btn"
            data-kind="primary"
            style={{ flex: 1 }}
            onClick={() => {
              // Resume the first unfinished lesson, not always lesson one.
              const target = course.lessons.find((lesson) => !lesson.completed) || course.lessons[0];
              if (target) onOpenLesson(target.id);
            }}
          >
            {finished > 0 ? `继续学习 (${finished}/${course.lesson_count})` : '开始学习'}
          </button>
        </div>
      </section>

      <section className="ef-section">
        <div className="ef-section-head">
          <h2 className="ef-section-title">课时</h2>
          <span style={{ fontSize: 13, color: 'var(--ef-ink-sub)' }}>
            已完成 {finished}/{course.lesson_count}
          </span>
        </div>
        {course.lessons.length === 0 ? (
          <EmptyState title="这门课还没有课时" description="扫描媒体库后会自动补齐。" />
        ) : (
          <div className="ef-list">
            {course.lessons.map((lesson) => (
              <LessonRow key={lesson.id} lesson={lesson} onOpen={() => onOpenLesson(lesson.id)} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function LessonRow({ lesson, onOpen }: { lesson: LessonBrief; onOpen: () => void }) {
  return (
    <button type="button" className="ef-row" onClick={onOpen}>
      <span className="ef-row-idx">{String(lesson.position).padStart(2, '0')}</span>
      <span className="ef-row-body">
        <span style={{ fontSize: 15, lineHeight: '21px', display: 'block' }}>{lesson.title}</span>
        <span className="ef-row-meta">
          <span className="ef-tag">{lesson.level}</span>
          <span>{fmtTime(lesson.duration)}</span>
          {lesson.has_subtitle && <span>· 字幕</span>}
        </span>
        {(lesson.progress || 0) > 0 && !lesson.completed && (
          <span className="ef-bar">
            <i style={{ width: `${Math.round(lesson.progress * 100)}%` }} />
          </span>
        )}
      </span>
      {lesson.completed && <span className="ef-done">完成</span>}
    </button>
  );
}
