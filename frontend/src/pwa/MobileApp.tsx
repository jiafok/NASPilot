import { useCallback, useEffect, useState } from 'react';
import './pwa.css';
import { TabBar, type TabKey } from './components';
import HomePage from './HomePage';
import DiscoverPage from './DiscoverPage';
import LearnPage from './LearnPage';
import AiPage, { recallLesson, rememberLesson } from './AiPage';
import MePage from './MePage';
import CoursePage from './CoursePage';
import PlayerPage from './PlayerPage';
import { FavoritesPage, StatsPage, WordsPage } from './SubPages';

/**
 * Mobile shell for the English Learning Center.
 *
 * Five fixed tabs, one full-screen player layer on top. Deliberately not a
 * re-skin of the desktop page: on a phone the player is the product, so it owns
 * the whole screen, and every list is a single column of 44dp-tall rows.
 */

type SubPage = 'stats' | 'words' | 'favorites' | null;

export default function MobileApp() {
  const [tab, setTab] = useState<TabKey>(() => (location.hash.replace('#', '') as TabKey) || 'home');
  const [courseId, setCourseId] = useState<number | null>(null);
  const [lessonId, setLessonId] = useState<number | null>(null);
  const [subPage, setSubPage] = useState<SubPage>(null);
  const [lastLesson, setLastLesson] = useState<number | null>(() => recallLesson());

  // The hash keeps a refresh on the same tab instead of snapping back home.
  useEffect(() => {
    const next = location.hash.replace('#', '') as TabKey;
    if (next && next !== tab) setTab(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const changeTab = useCallback((next: TabKey) => {
    setTab(next);
    setSubPage(null);
    setCourseId(null);
    history.replaceState(null, '', `#${next}`);
  }, []);

  const openLesson = useCallback((id: number) => {
    setLessonId(id);
    rememberLesson(id);
    setLastLesson(id);
  }, []);

  const openCourse = useCallback((id: number) => {
    setSubPage(null);
    setCourseId(id);
  }, []);

  // Android hardware back / browser back should close the topmost layer.
  useEffect(() => {
    const onPop = () => {
      if (lessonId) {
        setLessonId(null);
        return;
      }
      if (courseId) {
        setCourseId(null);
        return;
      }
      if (subPage) setSubPage(null);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [lessonId, courseId, subPage]);

  if (lessonId) {
    return (
      <PlayerPage
        lessonId={lessonId}
        onClose={() => {
          setLessonId(null);
          setLastLesson(lessonId);
        }}
        onOpenLesson={(id) => {
          setLessonId(id);
          rememberLesson(id);
          setLastLesson(id);
        }}
      />
    );
  }

  if (subPage === 'stats') {
    return (
      <div className="ef-app">
        <StatsPage onBack={() => setSubPage(null)} />
      </div>
    );
  }
  if (subPage === 'words') {
    return (
      <div className="ef-app">
        <WordsPage onBack={() => setSubPage(null)} />
      </div>
    );
  }
  if (subPage === 'favorites') {
    return (
      <div className="ef-app">
        <FavoritesPage onBack={() => setSubPage(null)} onOpenCourse={openCourse} />
      </div>
    );
  }

  if (courseId) {
    return (
      <div className="ef-app">
        <CoursePage
          courseId={courseId}
          onBack={() => setCourseId(null)}
          onOpenLesson={openLesson}
        />
      </div>
    );
  }

  return (
    <div className="ef-app">
      {tab === 'home' && (
        <HomePage
          onOpenLesson={openLesson}
          onOpenCourse={openCourse}
          onGoDiscover={() => changeTab('discover')}
          onGoMe={() => changeTab('me')}
        />
      )}
      {tab === 'discover' && <DiscoverPage onOpenCourse={openCourse} />}
      {tab === 'learn' && (
        <LearnPage
          onOpenLesson={openLesson}
          onOpenCourse={openCourse}
          onGoDiscover={() => changeTab('discover')}
        />
      )}
      {tab === 'ai' && <AiPage lessonId={lastLesson} onOpenLesson={openLesson} />}
      {tab === 'me' && (
        <MePage
          onOpenCourse={openCourse}
          onOpenStats={() => setSubPage('stats')}
          onOpenWords={() => setSubPage('words')}
          onOpenFavorites={() => setSubPage('favorites')}
        />
      )}

      <TabBar active={tab} onChange={changeTab} />
    </div>
  );
}
