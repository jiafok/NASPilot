import { useCallback, useEffect, useState } from 'react';
import { apiGet, errText, fmtTime, type Course, type StatsPayload } from './api';
import { ErrorState, SectionHead, SkeletonList } from './components';
import { useAuth } from '../hooks/useAuth';
import { CourseRow } from './HomePage';

interface Props {
  onOpenCourse: (courseId: number) => void;
  onOpenStats: () => void;
  onOpenWords: () => void;
  onOpenFavorites: () => void;
}

export default function MePage({ onOpenCourse, onOpenStats, onOpenWords, onOpenFavorites }: Props) {
  const { user, logout } = useAuth();
  const [stats, setStats] = useState<StatsPayload | null>(null);
  const [favorites, setFavorites] = useState<Course[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    Promise.all([
      apiGet.stats(90),
      apiGet.courses({ favorite: true, page_size: 5 }).catch(() => ({ items: [] as Course[] })),
    ])
      .then(([statsData, favData]) => {
        setStats(statsData);
        setFavorites(favData.items || []);
      })
      .catch((err) => setError(errText(err, '网络不可用')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  if (loading) {
    return (
      <div className="ef-page">
        <SkeletonList rows={1} />
        <div style={{ height: 24 }} />
        <SkeletonList rows={4} />
      </div>
    );
  }

  if (error || !stats) {
    return (
      <div className="ef-page">
        <ErrorState message={error || '没有数据'} onRetry={load} />
      </div>
    );
  }

  const { totals, streak, today, week } = stats;

  return (
    <div className="ef-page">
      <header className="ef-greeting">
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', minWidth: 0 }}>
          <span className="ef-avatar" style={{ width: 48, height: 48, borderRadius: 24, fontSize: 18 }}>
            {(user?.username || 'U').slice(0, 1).toUpperCase()}
          </span>
          <div style={{ minWidth: 0 }}>
            <h1 style={{ fontSize: 22 }}>{user?.username || '我'}</h1>
            <p>连续学习 {streak} 天 · 今天 {today.minutes} 分钟</p>
          </div>
        </div>
      </header>

      <section className="ef-section">
        <div className="ef-stat-grid">
          <div className="ef-stat">
            <b>{totals.lessons_completed}</b>
            <span>完成课时</span>
          </div>
          <div className="ef-stat">
            <b>{totals.listen_minutes}</b>
            <span>累计分钟</span>
          </div>
          <div className="ef-stat">
            <b>{week.days_active}</b>
            <span>本周学习天数</span>
          </div>
          <div className="ef-stat">
            <b>{streak}</b>
            <span>连续天数</span>
          </div>
        </div>
      </section>

      <section className="ef-section">
        <SectionHead title="学习热力图" action="详细统计" onAction={onOpenStats} />
        <Heatmap series={stats.series} />
        <div className="ef-legend">
          <span>少</span>
          {[0.15, 0.35, 0.55, 0.75, 1].map((alpha) => (
            <span
              key={alpha}
              className="ef-heat-cell"
              style={{ background: `rgba(37, 99, 235, ${alpha})` }}
            />
          ))}
          <span>多</span>
        </div>
      </section>

      <section className="ef-section">
        <SectionHead title="我的收藏" action="全部" onAction={onOpenFavorites} />
        {favorites.length === 0 ? (
          <p className="ef-sub">还没有收藏课程。在课程详情里点收藏即可。</p>
        ) : (
          <div className="ef-list">
            {favorites.map((course) => (
              <CourseRow key={course.id} course={course} onOpen={() => onOpenCourse(course.id)} />
            ))}
          </div>
        )}
      </section>

      <section className="ef-section">
        <div className="ef-list">
          <button type="button" className="ef-row" onClick={onOpenWords}>
            <span className="ef-row-body">
              <span style={{ fontSize: 15 }}>我的生词</span>
              <span className="ef-row-meta">点词加入的生词都收在这里</span>
            </span>
          </button>
          <button type="button" className="ef-row" onClick={onOpenStats}>
            <span className="ef-row-body">
              <span style={{ fontSize: 15 }}>学习统计</span>
              <span className="ef-row-meta">每日时长、成长曲线</span>
            </span>
          </button>
          <button type="button" className="ef-row" onClick={logout}>
            <span className="ef-row-body">
              <span style={{ fontSize: 15 }}>退出登录</span>
              <span className="ef-row-meta">会同时撤销本机的登录状态</span>
            </span>
          </button>
        </div>
      </section>

      <p className="ef-sub" style={{ textAlign: 'center', paddingBottom: 12 }}>
        累计静听 {fmtTime(totals.listen_seconds)}
      </p>
    </div>
  );
}

/** 12-week grid, one column per week, Monday-first. */
export function Heatmap({ series }: { series: StatsPayload['series'] }) {
  const weeks: StatsPayload['series'][] = [];
  for (let index = 0; index < series.length; index += 7) {
    weeks.push(series.slice(index, index + 7));
  }
  const max = Math.max(1, ...series.map((item) => item.seconds));

  return (
    <div className="ef-heatmap">
      {weeks.map((week, weekIndex) => (
        <div key={weekIndex} className="ef-heat-col">
          {week.map((day) => {
            const ratio = day.seconds / max;
            const alpha = day.seconds === 0 ? 0 : Math.max(0.18, Math.min(1, ratio));
            return (
              <span
                key={day.day}
                className="ef-heat-cell"
                title={`${day.day} · ${day.minutes} 分钟`}
                style={alpha ? { background: `rgba(37, 99, 235, ${alpha})` } : undefined}
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}
