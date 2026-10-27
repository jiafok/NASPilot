import { useCallback, useEffect, useRef, useState } from 'react';
import { apiGet, apiPost, errText } from './api';

interface Props {
  /** Lesson the user last opened; gives the tutor its context. */
  lessonId: number | null;
  onOpenLesson: (lessonId: number) => void;
}

interface Message {
  id: string;
  role: 'user' | 'ai';
  text: string;
  pending?: boolean;
  failed?: boolean;
}

const GENERIC_PROMPTS = [
  '怎么才能听懂母语者的连读？',
  '听不懂的时候应该反复听还是看字幕？',
  '每天练 20 分钟，怎么安排最有效？',
  '怎么提升口语的反应速度？',
  '听写和跟读，哪个更适合我？',
  '怎么记住一个词的不同用法？',
];

const CONTEXT_PROMPTS = ['这节课讲了什么？', '挑 3 个值得记的表达', '第一句的语法结构是什么？'];

export default function AiPage({ lessonId, onOpenLesson }: Props) {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [lessonTitle, setLessonTitle] = useState<string>('');
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    apiGet.aiStatus().then((status) => setConfigured(status.configured));
  }, []);

  useEffect(() => {
    if (!lessonId) {
      setLessonTitle('');
      return;
    }
    apiGet
      .lesson(lessonId)
      .then((lesson) => setLessonTitle(lesson.title))
      .catch(() => setLessonTitle(''));
  }, [lessonId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  /**
   * Ask the tutor, streaming the answer.
   *
   * Uses `fetch` rather than axios because the response is an SSE body that has
   * to be read incrementally; axios would buffer it and the user would sit on a
   * blank screen — exactly the problem this endpoint exists to solve.
   */
  const ask = useCallback(
    async (question: string) => {
      const trimmed = question.trim();
      if (!trimmed || busy) return;

      const userMessage: Message = { id: `u-${Date.now()}`, role: 'user', text: trimmed };
      const aiId = `a-${Date.now()}`;
      setMessages((prev) => [...prev, userMessage, { id: aiId, role: 'ai', text: '', pending: true }]);
      setInput('');
      setBusy(true);

      const patch = (updater: (message: Message) => Message) =>
        setMessages((prev) => prev.map((message) => (message.id === aiId ? updater(message) : message)));

      try {
        const token = localStorage.getItem('token') || '';
        const response = await fetch('/api/english/ai/chat/stream', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            Authorization: `Bearer ${token}`,
          },
          body: JSON.stringify({ lesson_id: lessonId ?? 0, question: trimmed }),
        });

        if (!response.ok || !response.body) {
          const detail = await response.text().catch(() => '');
          throw new Error(detail || `请求失败 (${response.status})`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // Events are separated by a blank line.
          const frames = buffer.split('\n\n');
          buffer = frames.pop() || '';

          for (const frame of frames) {
            const eventLine = frame.split('\n').find((line) => line.startsWith('event:'));
            const dataLine = frame.split('\n').find((line) => line.startsWith('data:'));
            if (!dataLine) continue;
            const event = eventLine ? eventLine.slice(6).trim() : 'delta';
            let payload: { text?: string; detail?: string } = {};
            try {
              payload = JSON.parse(dataLine.slice(5).trim());
            } catch {
              continue;
            }

            if (event === 'delta' && payload.text) {
              const text = payload.text;
              patch((message) => ({ ...message, text: message.text + text, pending: true }));
            } else if (event === 'done') {
              patch((message) => ({ ...message, pending: false }));
            } else if (event === 'error') {
              patch((message) => ({
                ...message,
                pending: false,
                failed: true,
                text: payload.detail || 'AI 暂时不可用',
              }));
            }
          }
        }

        patch((message) => ({
          ...message,
          pending: false,
          text: message.text || '（没有返回内容）',
        }));
      } catch (err) {
        const fallback = errText(err, '（网络中断，请重试）');
        patch((message) => ({ ...message, pending: false, failed: true, text: fallback }));
      } finally {
        setBusy(false);
      }
    },
    [busy, lessonId],
  );

  const prompts = lessonId ? CONTEXT_PROMPTS : GENERIC_PROMPTS;

  return (
    <div className="ef-page" style={{ paddingBottom: 120 }}>
      <div className="ef-greeting" style={{ paddingBottom: 8 }}>
        <div>
          <h1 style={{ fontSize: 24 }}>AI 老师</h1>
          <p>结合字幕内容回答问题，可以一直追问。</p>
        </div>
      </div>

      {configured === false && (
        <div className="ef-notice">
          AI 还没有配置。请管理员在「系统设置 → AI」填写 API Key 后再使用对话。
        </div>
      )}

      {lessonId && lessonTitle && (
        <div className="ef-card" style={{ marginBottom: 12, cursor: 'default' }}>
          <p className="ef-sub">当前上下文</p>
          <p className="ef-title-2" style={{ margin: '4px 0 0' }}>
            {lessonTitle}
          </p>
          <button
            type="button"
            className="ef-btn"
            style={{ marginTop: 10 }}
            onClick={() => onOpenLesson(lessonId)}
          >
            回到这一课
          </button>
        </div>
      )}

      {messages.length === 0 && (
        <section className="ef-section">
          <div className="ef-section-head">
            <h2 className="ef-section-title">可以这样问</h2>
          </div>
          <div className="ef-list">
            {prompts.map((prompt) => (
              <button
                key={prompt}
                type="button"
                className="ef-row"
                disabled={configured === false}
                onClick={() => void ask(prompt)}
              >
                <span className="ef-row-body">
                  <span style={{ fontSize: 15, lineHeight: '22px' }}>{prompt}</span>
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      <div className="ef-ai-msgs">
        {messages.map((message) => (
          <div
            key={message.id}
            className="ef-msg"
            data-role={message.role}
            style={message.failed ? { borderColor: 'var(--ef-danger)', color: 'var(--ef-danger)' } : undefined}
          >
            {message.text}
            {message.pending && <span style={{ opacity: 0.55 }}>▍</span>}
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <form
        className="ef-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void ask(input);
        }}
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={configured === false ? 'AI 未配置' : '问点什么…'}
          disabled={configured === false || busy}
          enterKeyHint="send"
        />
        <button
          type="submit"
          className="ef-btn"
          data-kind="primary"
          disabled={configured === false || busy || !input.trim()}
        >
          {busy ? '…' : '发送'}
        </button>
      </form>
    </div>
  );
}

/** Keep the last opened lesson so the AI tab has context across app restarts. */
export function rememberLesson(lessonId: number) {
  localStorage.setItem('ef_last_lesson', String(lessonId));
}

export function recallLesson(): number | null {
  const raw = localStorage.getItem('ef_last_lesson');
  const value = raw ? Number(raw) : NaN;
  return Number.isFinite(value) && value > 0 ? value : null;
}

/** Fire-and-forget helper used by the stats page to keep totals honest. */
export function reportCompletion(lessonId: number) {
  void apiPost.progress({ lesson_id: lessonId, position: 0, completed: true }).catch(() => undefined);
}
