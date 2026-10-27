import { useCallback, useEffect, useState } from 'react';
import { apiGet, errText, fmtTime, withToken, type Course, type HomeData } from './api';
import {
  EmptyState,
  ErrorState,
  IconPlay,
  SectionHead,
  SkeletonList,
} from './components';

interface Props {
  onOpenLesson: (lessonId: number) => void;
  onOpenCourse: (courseId: number) => void;
  onGoDiscover: () => void;
  onGoMe: () => void;
}

export default function HomePage({ onOpenLesson, onOpenCourse, onGoDiscover, onGoMe }: Props) {
  const [data, setData] = useState<HomeData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    apiGet
      .home()
      .then(setData)
      .catch((err) => setError(errText(err, '网络不可用')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  if (loading) {
    return (
      <div className="ef-page">
        <div className="ef-greeting">
          <div>
            <div className="ef-skel" style={{ height: 34, width: 200 }} />
            <div className="ef-skel" style={{ height: 16, width: 140, marginTop: 10 }} />
          </div>
        </div>
        <SkeletonList rows={1} />
        <div style={{ height: 28 }} />
        <SkeletonList rows={3} />
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="ef-page">
        <ErrorState message={error || '没有数据'} onRetry={load} />
      </div>
    );
  }

  const { greeting, stats, continue_learning: continueLearning, recommended, recent } = data;
  const next = continueLearning[0];

  return (
    <div className="ef-page">
      <header className="ef-greeting">
        <div style={{ minWidth: 0 }}>
          <h1>{greeting}</h1>
          <p>
            {stats.completed > 0
              ? `今天已学 ${stats.minutes_today} 分钟 · 累计完成 ${stats.completed} 课`
              : "Let's start your first lesson"}
          </p>
        </div>
        <button type="button" className="ef-avatar" onClick={onGoMe} aria-label="我的">
          {(data.user?.name || 'U').slice(0, 1).toUpperCase()}
        </button>
      </header>

      {/* Section 2 — Continue learning. Falls back to a starter card so the
          first screen is never empty for a new account. */}
      <section className="ef-section">
        {next ? (
          <button
            type="button"
            className="ef-card ef-continue"
            onClick={() => onOpenLesson(next.lesson_id)}
          >
            <span className="ef-cover">
              {next.cover ? <img src={withToken(next.cover)} alt="" /> : <IconPlay />}
            </span>
            <span className="ef-continue-body">
              <p className="ef-title-2">继续学习 · {next.lesson_title}</p>
              <p className="ef-sub">
                {next.course_title} · {fmtTime(next.position)} / {fmtTime(next.duration)}
              </p>
              <span className="ef-bar">
                <i style={{ width: `${Math.round((next.progress || 0) * 100)}%` }} />
              </span>
            </span>
          </button>
        ) : recommended.length > 0 ? (
          <button
            type="button"
            className="ef-card ef-continue"
            onClick={() => onOpenCourse(recommended[0].id)}
          >
            <span className="ef-cover">
              {recommended[0].cover ? <img src={withToken(recommended[0].cover)} alt="" /> : <IconPlay />}
            </span>
            <span className="ef-continue-body">
              <p className="ef-title-2">从这里开始</p>
              <p className="ef-sub">
                {recommended[0].title} · {recommended[0].lesson_count} 课
              </p>
              <span className="ef-bar">
                <i style={{ width: '0%' }} />
              </span>
            </span>
          </button>
        ) : (
          <EmptyState
            title="课程库还是空的"
            description="管理员扫描媒体库后，课程会自动出现在这里。"
          />
        )}
      </section>

      {continueLearning.length > 1 && (
        <section className="ef-section">
          <SectionHead title="进行中" />
          <div className="ef-list">
            {continueLearning.slice(1, 4).map((item) => (
              <button
                key={item.lesson_id}
                type="button"
                className="ef-row"
                onClick={() => onOpenLesson(item.lesson_id)}
              >
                <span className="ef-row-body">
                  <span className="ef-title-2" style={{ margin: 0 }}>
                    {item.lesson_title}
                  </span>
                  <span className="ef-row-meta">
                    {item.course_title} · {fmtTime(item.position)} / {fmtTime(item.duration)}
                  </span>
                </span>
                <span className="ef-done">{Math.round((item.progress || 0) * 100)}%</span>
              </button>
            ))}
          </div>
        </section>
      )}

      {recommended.length > 0 && (
        <section className="ef-section">
          <SectionHead title="适合你" action="去发现" onAction={onGoDiscover} />
          <div className="ef-list">
            {recommended.slice(0, 5).map((course) => (
              <CourseRow key={course.id} course={course} onOpen={() => onOpenCourse(course.id)} />
            ))}
          </div>
        </section>
      )}

      {recent.length > 0 && (
        <section className="ef-section">
          <SectionHead title="最近更新" />
          <div className="ef-list">
            {recent.slice(0, 5).map((course) => (
              <CourseRow key={course.id} course={course} onOpen={() => onOpenCourse(course.id)} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

export function CourseRow({ course, onOpen }: { course: Course; onOpen: () => void }) {
  return (
    <button type="button" className="ef-row" onClick={onOpen}>
      <span className="ef-cover" style={{ width: 64, height: 44, fontSize: 14 }}>
        {course.cover ? <img src={withToken(course.cover)} alt="" /> : null}
      </span>
      <span className="ef-row-body">
        <span className="ef-title-2" style={{ margin: 0 }}>
          {course.title}
        </span>
        <span className="ef-row-meta">
          <span className="ef-tag">{course.level}</span>
          {course.topics?.slice(0, 2).map((topic) => (
            <span key={topic} className="ef-tag" data-tone="plain">
              {topic}
            </span>
          ))}
          <span>{course.lesson_count} 课</span>
        </span>
      </span>
    </button>
  );
}
