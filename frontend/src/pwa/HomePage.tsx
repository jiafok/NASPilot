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

  useEffect(() => {
    window.addEventListener('english-progress-updated', load);
    return () => window.removeEventListener('english-progress-updated', load);
  }, [load]);

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
  const featured = recommended[0] || recent[0];
  const featuredCover = next?.cover || featured?.cover;

  return (
    <div className="ef-page ef-home">
      <header className="ef-greeting ef-home-heading">
        <div style={{ minWidth: 0 }}>
          <span className="ef-home-brand">NASPILOT · LEARNING</span>
          <h1>EnglishFlow</h1>
          <p>
            {greeting}
          </p>
        </div>
        <button type="button" className="ef-avatar" onClick={onGoMe} aria-label="我的">
          {(data.user?.name || 'U').slice(0, 1).toUpperCase()}
        </button>
      </header>

      <section className="ef-section ef-home-feature">
        {next || featured ? (
          <button
            type="button"
            className="ef-feature-scene"
            onClick={() => next ? onOpenLesson(next.lesson_id) : featured && onOpenCourse(featured.id)}
          >
            <img className="ef-feature-image" src={withToken(featuredCover) || COURSE_PLACEHOLDER} alt="" />
            <span className="ef-feature-shade" />
            <span className="ef-feature-content">
              <span className="ef-feature-eyebrow">{next ? '继续学习' : '今日精选'}</span>
              <strong>{next?.lesson_title || featured?.title}</strong>
              <span className="ef-feature-meta">
                {next ? `${next.course_title} · ${fmtTime(next.position)} / ${fmtTime(next.duration)}` : `${featured?.level} · ${featured?.lesson_count} 课时`}
              </span>
            </span>
            <span className="ef-feature-play" aria-hidden="true"><IconPlay /></span>
            <span className="ef-feature-progress">
              <i style={{ width: `${Math.round((next?.progress || 0) * 100)}%` }} />
            </span>
          </button>
        ) : (
          <EmptyState
            title="课程库还是空的"
            description="管理员扫描媒体库后，课程会自动出现在这里。"
          />
        )}
      </section>

      <section className="ef-home-numbers" aria-label="学习概览">
        <div><strong>{stats.minutes_today}<small> min</small></strong><span>今日学习</span></div>
        <div><strong>{stats.completed}</strong><span>已完成课时</span></div>
        <div><strong>{stats.courses}</strong><span>可学课程</span></div>
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
          <div className="ef-course-grid">
            {recommended.slice(0, 6).map((course) => (
              <CourseTile key={course.id} course={course} onOpen={() => onOpenCourse(course.id)} />
            ))}
          </div>
        </section>
      )}

      {recent.length > 0 && (
        <section className="ef-section">
          <SectionHead title="最近更新" />
          <div className="ef-course-grid">
            {recent.slice(0, 6).map((course) => (
              <CourseTile key={course.id} course={course} onOpen={() => onOpenCourse(course.id)} />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

const COURSE_PLACEHOLDER = '/learning-cover.jpg';

export function CourseTile({ course, onOpen }: { course: Course; onOpen: () => void }) {
  return (
    <button className="ef-course-tile" type="button" onClick={onOpen}>
      <span className="ef-course-art">
        <img src={withToken(course.cover) || COURSE_PLACEHOLDER} alt="" loading="lazy" />
        <span className="ef-course-level">{course.level}</span>
        <span className="ef-course-play" aria-hidden="true"><IconPlay /></span>
      </span>
      <span className="ef-course-info">
        <span className="ef-course-channel">{course.channel}</span>
        <strong>{course.title}</strong>
        <span>{course.lesson_count} 课时 · {fmtTime(course.total_seconds)}</span>
      </span>
    </button>
  );
}

export function CourseRow({ course, onOpen }: { course: Course; onOpen: () => void }) {
  return (
    <button type="button" className="ef-row" onClick={onOpen}>
      <span className="ef-cover" style={{ width: 88, height: 66, fontSize: 14 }}>
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
