import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { KeyRound } from "lucide-react";
import { useDemoPersonas } from "../api/hooks";
import type { DemoPersona, DemoPersonaGroup } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { safeNext } from "../auth/safeNext";
import { Button, ButtonLink } from "../components/Button";
import { ErrorPanel } from "../components/ErrorPanel";
import { Field, TextInput } from "../components/FormField";
import { useStrings } from "../i18n";
import { Stripe } from "../components/Stripe";
import { AsideBlock, AuthLayout } from "./AuthLayout";

const DEMO = { email: "anna.korhonen@hotel.fi", password: "demo1234" };

export default function LoginPage() {
  const strings = useStrings();
  const t = strings.auth;
  const l = strings.login;
  const { login, isAuthenticated } = useAuth();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const mocks = import.meta.env.VITE_USE_MOCKS === "true";
  const personasQuery = useDemoPersonas();
  const personas: DemoPersona[] = personasQuery.data?.items?.length
    ? personasQuery.data.items
    : mocks
      ? [{ email: DEMO.email, name: "Anna Korhonen", company_name: "Hotel Aurora Oy", group: "demo", role_label: "Hotel F&B Buyer" }]
      : [];
  const demoItems = personas.filter((p) => (p.group ?? "demo") === "demo");
  const personaGroups: { key: DemoPersonaGroup; title: string; items: DemoPersona[] }[] =
    demoItems.length > 0 ? [{ key: "demo", title: t.personaGroupDemo, items: demoItems }] : [];
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({});
  const [apiError, setApiError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const next = safeNext(params.get("next"));
  const expired = params.get("expired") === "1";

  if (isAuthenticated) return <Navigate to={next} replace />;

  const validate = () => {
    const e: typeof errors = {};
    if (!email.trim()) e.email = t.errRequired(t.email);
    else if (!/^\S+@\S+\.\S+$/.test(email.trim())) e.email = t.errEmail;
    if (!password) e.password = t.errRequired(t.password);
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const quickFill = (targetEmail: string) => {
    setEmail(targetEmail);
    setPassword(DEMO.password);
    setErrors({});
    setApiError(null);
  };

  const quickLogin = async (targetEmail: string) => {
    if (busy) return;
    setEmail(targetEmail);
    setPassword(DEMO.password);
    setErrors({});
    setApiError(null);
    setBusy(true);
    try {
      await login({ email: targetEmail.trim(), password: DEMO.password });
      navigate(next, { replace: true });
    } catch (err) {
      setApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setApiError(null);
    if (!validate()) return;
    setBusy(true);
    try {
      await login({ email: email.trim(), password });
      navigate(next, { replace: true });
    } catch (err) {
      setApiError(err);
    } finally {
      setBusy(false);
    }
  };

  const aside = (
    <>
      <AsideBlock title={l.whatTitle}>
        <ul className="space-y-2">
          {l.whatLines.map((x) => (
            <li key={x}>{x}</li>
          ))}
        </ul>
      </AsideBlock>
      <AsideBlock title={l.newCustomer}>
        <p>{l.newCustomerBody}</p>
        <ButtonLink to="/register" variant="outline" size="lg" className="mt-8 w-full">
          {l.register}
        </ButtonLink>
      </AsideBlock>
    </>
  );

  return (
    <AuthLayout aside={aside}>
      <h1 className="font-display text-[40px] leading-[44px] tracking-[0.01em] text-navy">{l.welcome}</h1>
      <p className="mt-6 text-[18px] font-bold leading-[22px] text-navy">{l.lead}</p>
      {expired && apiError == null && (
        <Stripe kind="warning" compact className="mt-6">
          {t.sessionExpired}
        </Stripe>
      )}
      <form onSubmit={submit} noValidate className="mt-8 space-y-6">
        <Field label={t.email} error={errors.email}>
          {(p) => (
            <TextInput
              {...p}
              type="email"
              autoComplete="username"
              inputMode="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                if (errors.email) setErrors((x) => ({ ...x, email: undefined }));
              }}
            />
          )}
        </Field>
        <Field label={t.password} error={errors.password}>
          {(p) => (
            <TextInput
              {...p}
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                if (errors.password) setErrors((x) => ({ ...x, password: undefined }));
              }}
            />
          )}
        </Field>
        {apiError != null && <ErrorPanel error={apiError} />}
        <Button type="submit" variant="lime" size="lg" loading={busy}>
          {t.loginCta}
        </Button>
      </form>
      {personaGroups.length > 0 && (
        <div
          className="mt-8 min-w-0 max-w-full overflow-hidden border border-line-strong bg-primary-soft/50 p-3"
          data-testid="demo-personas-panel"
        >
          <div className="flex min-w-0 items-center gap-1.5 text-[13px] font-bold text-navy">
            <KeyRound className="h-3.5 w-3.5 shrink-0 text-navy" strokeWidth={1.75} aria-hidden="true" />
            <span className="min-w-0 break-words">{t.demoHint}</span>
          </div>
          <p className="mt-1 text-[11.5px] leading-4 text-ink-secondary">{t.demoSubhint}</p>
          <div className="mt-3 space-y-3.5">
            {personaGroups.map((group) => (
              <section
                key={group.key}
                data-testid={`persona-group-${group.key}`}
                className="min-w-0 max-w-full"
              >
                <h3 className="mb-1.5 flex min-w-0 items-center gap-1.5 text-[11px] font-extrabold uppercase leading-4 tracking-[0.04em] text-navy">
                  <span className="min-w-0 break-words">{group.title}</span>
                </h3>
                <div className="space-y-1.5">
                  {group.items.map((persona) => (
                    <div
                      key={persona.email}
                      className="flex min-w-0 max-w-full items-stretch border border-line bg-white transition-colors hover:border-navy"
                    >
                      <button
                        type="button"
                        data-testid={`demo-persona-${persona.email}`}
                        onClick={() => quickFill(persona.email)}
                        className="flex min-w-0 flex-1 flex-col items-start gap-0.5 overflow-hidden px-2.5 py-2 text-left text-xs transition-colors hover:bg-primary-soft/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-navy"
                      >
                        <span className="block w-full truncate text-[12.5px] font-bold leading-4 text-navy">
                          {persona.name}
                        </span>
                        {persona.role_label && (
                          <span className="inline-block max-w-full truncate rounded-none border border-line bg-primary-soft px-1.5 py-0.5 text-[10px] font-bold uppercase leading-3.5 tracking-[0.03em] text-ink-secondary">
                            {persona.role_label}
                          </span>
                        )}
                        <span className="block w-full truncate text-[11px] font-semibold leading-4 text-ink">
                          {persona.company_name}
                        </span>
                        <span className="font-mono-code block w-full truncate text-[10.5px] leading-3.5 text-ink-secondary">
                          {persona.email}
                        </span>
                      </button>
                      <button
                        type="button"
                        data-testid={`demo-persona-signin-${persona.email}`}
                        disabled={busy}
                        title={`${t.demoSignIn} (${persona.name})`}
                        onClick={() => void quickLogin(persona.email)}
                        className="shrink-0 whitespace-nowrap border-l border-line bg-primary-soft/75 px-2.5 py-2 text-[11.5px] font-bold text-navy transition-colors hover:bg-lime hover:text-navy focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-navy disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {t.demoSignIn}
                      </button>
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </div>
      )}
    </AuthLayout>
  );
}
