import { useCallback, useEffect, useState } from 'react';
import { apiGet, errText, type Course, type StatsPayload } from './api';
import { EmptyState, ErrorState, IconBack, SkeletonList } from './components';
import { Heatmap } from './MePage';
import { CourseTile } from './HomePage';
import { listWords, removeWord } from './wordStore';
import { DeleteOutlined, SearchOutlined } from '@ant-design/icons';

function SubHeader({ title, onBack }: { title: string; onBack: () => void }) {
  return (
    <div className="ef-subheader">
      <button type="button" className="ef-icon-btn" onClick={onBack} aria-label="返回" style={{ color: 'inherit' }}>
        <IconBack />
      </button>
      <h1 className="ef-section-title" style={{ fontSize: 18 }}>
        {title}
      </h1>
      <span className="ef-subheader-spacer" />
    </div>
  );
}

// ── Stats ────────────────────────────────────────────────────────────────

export function StatsPage({ onBack }: { onBack: () => void }) {
  const [stats, setStats] = useState<StatsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    setError('');
    apiGet
      .stats(90)
      .then(setStats)
      .catch((err) => setError(errText(err, '网络不可用')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  if (loading) {
    return (
      <div className="ef-page">
        <SubHeader title="学习统计" onBack={onBack} />
        <SkeletonList rows={3} />
      </div>
    );
  }
  if (error || !stats) {
    return (
      <div className="ef-page">
        <SubHeader title="学习统计" onBack={onBack} />
        <ErrorState message={error || '没有数据'} onRetry={load} />
      </div>
    );
  }

  const maxSeconds = Math.max(1, ...stats.series.map((item) => item.seconds));

  return (
    <div className="ef-page">
      <SubHeader title="学习统计" onBack={onBack} />

      <section className="ef-section">
        <div className="ef-stat-grid">
          <div className="ef-stat">
            <b>{stats.streak}</b>
            <span>连续天数</span>
          </div>
          <div className="ef-stat">
            <b>{stats.week.minutes}</b>
            <span>本周分钟</span>
          </div>
          <div className="ef-stat">
            <b>{stats.totals.lessons_completed}</b>
            <span>完成课时</span>
          </div>
          <div className="ef-stat">
            <b>{stats.totals.active_days}</b>
            <span>有学习的天数</span>
          </div>
        </div>
      </section>

      <section className="ef-section">
        <div className="ef-section-head">
          <h2 className="ef-section-title">近 90 天</h2>
          <span style={{ fontSize: 13, color: 'var(--ef-ink-sub)' }}>
            共 {stats.month.minutes} 分钟（近 30 天）
          </span>
        </div>
        <Heatmap series={stats.series} />
      </section>

      <section className="ef-section">
        <div className="ef-section-head">
          <h2 className="ef-section-title">每日时长</h2>
        </div>
        <div className="ef-activity-chart">
          {stats.series.slice(-30).map((day) => (
            <span
              key={day.day}
              title={`${day.day} · ${day.minutes} 分钟`}
              style={{
                flex: 1,
                height: `${Math.max(2, (day.seconds / maxSeconds) * 100)}%`,
                background:
                  day.seconds > 0 ? 'var(--ef-primary)' : 'rgba(148, 163, 184, 0.25)',
                borderRadius: 3,
              }}
            />
          ))}
        </div>
        <p className="ef-sub" style={{ marginTop: 8 }}>
          最近 30 天
        </p>
      </section>

      <section className="ef-section">
        <div className="ef-section-head">
          <h2 className="ef-section-title">已完成的难度分布</h2>
        </div>
        {Object.keys(stats.levels).length === 0 ? (
          <p className="ef-sub">完成第一节课后，这里会显示你的难度分布。</p>
        ) : (
          <div className="ef-list">
            {Object.entries(stats.levels)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([level, count]) => (
                <div key={level} className="ef-row" style={{ cursor: 'default' }}>
                  <span className="ef-tag">{level}</span>
                  <span className="ef-row-body">
                    <span className="ef-bar">
                      <i
                        style={{
                          width: `${Math.round(
                            (count / Math.max(1, stats.totals.lessons_completed)) * 100,
                          )}%`,
                        }}
                      />
                    </span>
                  </span>
                  <span style={{ fontSize: 13, color: 'var(--ef-ink-sub)' }}>{count} 课</span>
                </div>
              ))}
          </div>
        )}
      </section>
    </div>
  );
}

// ── Favorites ────────────────────────────────────────────────────────────

export function FavoritesPage({
  onBack,
  onOpenCourse,
}: {
  onBack: () => void;
  onOpenCourse: (courseId: number) => void;
}) {
  const [courses, setCourses] = useState<Course[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    apiGet
      .courses({ favorite: true, page_size: 50 })
      .then((data) => setCourses(data.items || []))
      .catch((err) => setError(errText(err, '网络不可用')))
      .finally(() => setLoading(false));
  }, []);

  useEffect(load, [load]);

  return (
    <div className="ef-page">
      <SubHeader title="我的收藏" onBack={onBack} />
      {loading ? (
        <SkeletonList rows={4} />
      ) : error ? (
        <ErrorState message={error} onRetry={load} />
      ) : courses.length === 0 ? (
        <EmptyState title="还没有收藏" description="在课程详情页点「收藏」就会出现在这里。" />
      ) : (
        <div className="ef-course-grid">
          {courses.map((course) => (
            <CourseTile key={course.id} course={course} onOpen={() => onOpenCourse(course.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

// ── Words ────────────────────────────────────────────────────────────────

export function WordsPage({ onBack }: { onBack: () => void }) {
  const [words, setWords] = useState(listWords());
  const [query, setQuery] = useState('');
  const filtered = words.filter((item) => `${item.word} ${item.meaning} ${item.sentence || ''} ${(item.examples || []).join(' ')}`.toLowerCase().includes(query.toLowerCase()));

  return (
    <div className="ef-page">
      <SubHeader title="我的生词" onBack={onBack} />
      <div className="ef-vocabulary-count"><strong>{words.length}</strong><span>个表达，慢慢积累。</span></div>
      <div className="ef-search"><SearchOutlined /><input aria-label="搜索生词" placeholder="搜索单词或释义" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
      <div className="ef-notice">
        仅本机保存 · 尚未同步
      </div>

      {words.length === 0 ? (
        <EmptyState
          title="还没有生词"
          description="把遇见的好词，留在这里。"
        />
      ) : (
        <div className="ef-list ef-word-list">
          {filtered.map((item) => (
            <div key={item.word} className="ef-row" style={{ cursor: 'default' }}>
              <span className="ef-row-body">
                <span style={{ fontSize: 16, fontWeight: 600 }}>{item.word}</span>
                {item.phonetic && (
                  <span className="ef-sub" style={{ marginLeft: 6 }}>
                    {item.phonetic}
                  </span>
                )}
                <span className="ef-row-meta">{item.meaning}</span>
                {(item.examples?.length || item.sentence) && (
                  <details className="ef-saved-examples">
                    <summary>查看例句和原文</summary>
                    {item.examples?.slice(0, 2).map((example, index) => (
                      <p key={index}><strong>例句 {index + 1}</strong>{example}</p>
                    ))}
                    {item.sentence && <p><strong>原文句子</strong>{item.sentence}</p>}
                  </details>
                )}
              </span>
              <button
                type="button"
                className="ef-icon-btn ef-word-delete"
                aria-label={`删除 ${item.word}`}
                title="删除生词"
                onClick={() => setWords(removeWord(item.word))}
              >
                <DeleteOutlined />
              </button>
            </div>
          ))}
          {filtered.length === 0 && <p className="ef-empty">没有匹配的生词</p>}
        </div>
      )}
    </div>
  );
}
