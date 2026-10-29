import { useCallback, useEffect, useState, type ReactNode } from 'react';
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
  const [playerMinimized, setPlayerMinimized] = useState(false);
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
    setPlayerMinimized(false);
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
        if (playerMinimized) {
          setLessonId(null);
          return;
        }
        setPlayerMinimized(true);
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
  }, [lessonId, playerMinimized, courseId, subPage]);

  let page: ReactNode;
  if (subPage === 'stats') page = <StatsPage onBack={() => setSubPage(null)} />;
  else if (subPage === 'words') page = <WordsPage onBack={() => setSubPage(null)} />;
  else if (subPage === 'favorites') page = <FavoritesPage onBack={() => setSubPage(null)} onOpenCourse={openCourse} />;
  else if (courseId) page = <CoursePage courseId={courseId} onBack={() => setCourseId(null)} onOpenLesson={openLesson} />;
  else if (tab === 'home') page = <HomePage onOpenLesson={openLesson} onOpenCourse={openCourse} onGoDiscover={() => changeTab('discover')} onGoMe={() => changeTab('me')} />;
  else if (tab === 'discover') page = <DiscoverPage onOpenCourse={openCourse} />;
  else if (tab === 'learn') page = <LearnPage onOpenLesson={openLesson} onGoDiscover={() => changeTab('discover')} />;
  else if (tab === 'ai') page = <AiPage lessonId={lastLesson} onOpenLesson={openLesson} />;
  else page = <MePage onOpenCourse={openCourse} onOpenStats={() => setSubPage('stats')} onOpenWords={() => setSubPage('words')} onOpenFavorites={() => setSubPage('favorites')} />;

  return (
    <>
      <div className="ef-app" data-mini-player={Boolean(lessonId && playerMinimized)}>
        {page}
        <TabBar active={tab} onChange={changeTab} />
      </div>
      {lessonId !== null && (
        <PlayerPage
          lessonId={lessonId}
          minimized={playerMinimized}
          onClose={() => { setPlayerMinimized(true); window.dispatchEvent(new Event('english-progress-updated')); }}
          onExpand={() => setPlayerMinimized(false)}
          onStop={() => { setLessonId(null); setPlayerMinimized(false); }}
          onOpenLesson={(id) => { setLessonId(id); setPlayerMinimized(false); rememberLesson(id); setLastLesson(id); }}
        />
      )}
    </>
  );
}
