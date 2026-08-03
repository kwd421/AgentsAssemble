import { useMemo, useState } from "react";
import {
  ExternalLink,
  HelpCircle,
  LoaderCircle,
  ShieldAlert,
} from "lucide-react";
import type {
  PendingProviderRequest,
  ProviderRequestResolution,
} from "../../lib/providerRequestModel";
import "./ProviderRequestCard.css";

export default function ProviderRequestCard({
  request,
  onResolve,
}: {
  request: PendingProviderRequest;
  onResolve: (
    providerRequestId: string,
    resolution: ProviderRequestResolution
  ) => Promise<void>;
}) {
  const [answers, setAnswers] = useState<Record<string, string[]>>({});
  const [customAnswers, setCustomAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const waiting = request.status === "resolving" || submitting;
  const allAnswered = useMemo(
    () =>
      request.questions.every(
        (question) => answerValues(question.id, question.multiple).length > 0
      ),
    [answers, customAnswers, request.questions]
  );

  function answerValues(questionId: string, multiple = false) {
    const selected = answers[questionId] || [];
    const custom = String(customAnswers[questionId] || "").trim();
    if (!custom) return selected;
    return multiple ? [...selected, custom] : [custom];
  }

  async function resolve(resolution: ProviderRequestResolution) {
    if (waiting) return;
    setSubmitting(true);
    setError("");
    try {
      await onResolve(request.provider_request_id, resolution);
    } catch (value) {
      setError(value instanceof Error ? value.message : "요청을 전달하지 못했습니다.");
      setSubmitting(false);
    }
  }

  function submitAnswers() {
    if (!allAnswered) return;
    void resolve({
      answers: Object.fromEntries(
        request.questions.map((question) => [
          question.id,
          answerValues(question.id, question.multiple),
        ])
      ),
    });
  }

  const Icon = request.request_kind === "permission" ? ShieldAlert : HelpCircle;
  return (
    <section
      className="dc-provider-request"
      data-kind={request.request_kind}
      aria-live="polite"
    >
      <header className="dc-provider-request-header">
        <span className="dc-provider-request-icon" aria-hidden>
          {waiting ? <LoaderCircle className="dc-provider-request-spinner" size={18} /> : <Icon size={18} />}
        </span>
        <div>
          <strong>{request.title}</strong>
          <span>
            {request.display_name || request.provider_kind || "Agent Session"}
            {waiting ? " · 응답 전달 중" : " · 응답 필요"}
          </span>
        </div>
      </header>

      {request.description && (
        <p className="dc-provider-request-description">{request.description}</p>
      )}

      {request.response_kind === "option" && (
        <div className="dc-provider-request-actions">
          {request.options.map((option) => (
            <button
              type="button"
              key={option.id}
              disabled={waiting}
              data-kind={option.kind}
              title={option.description || undefined}
              onClick={() => void resolve({ option_id: option.id })}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}

      {request.response_kind === "answers" && (
        <div className="dc-provider-request-questions">
          {request.questions.map((question) => (
            <fieldset key={question.id} disabled={waiting}>
              <legend>{question.header || question.question}</legend>
              {question.header && <p>{question.question}</p>}
              {question.options.length > 0 && (
                <div className="dc-provider-request-choices">
                  {question.options.map((option) => (
                    <button
                      type="button"
                      key={option.id}
                      aria-pressed={(answers[question.id] || []).includes(option.label)}
                      data-selected={(answers[question.id] || []).includes(option.label)}
                      onClick={() =>
                        setAnswers((previous) => {
                          const selected = previous[question.id] || [];
                          const next = question.multiple
                            ? selected.includes(option.label)
                              ? selected.filter((value) => value !== option.label)
                              : [...selected, option.label]
                            : [option.label];
                          return { ...previous, [question.id]: next };
                        })
                      }
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
              )}
              {(question.is_other || question.options.length === 0) && (
                <input
                  type={question.is_secret ? "password" : "text"}
                  value={customAnswers[question.id] || ""}
                  placeholder={question.is_other ? "직접 입력" : "답변 입력"}
                  onChange={(event) =>
                    setCustomAnswers((previous) => ({
                      ...previous,
                      [question.id]: event.target.value,
                    }))
                  }
                />
              )}
            </fieldset>
          ))}
          <div className="dc-provider-request-actions">
            <button type="button" disabled={waiting || !allAnswered} onClick={submitAnswers}>
              답변 보내기
            </button>
          </div>
        </div>
      )}

      {request.response_kind === "acknowledge" && (
        <div className="dc-provider-request-actions">
          {request.action_url && (
            <a href={request.action_url} target="_blank" rel="noreferrer">
              필요한 페이지 열기 <ExternalLink size={14} />
            </a>
          )}
          <button
            type="button"
            disabled={waiting}
            onClick={() => void resolve({ acknowledged: true })}
          >
            완료했어요
          </button>
        </div>
      )}

      {error && <p className="dc-provider-request-error">{error}</p>}
    </section>
  );
}
