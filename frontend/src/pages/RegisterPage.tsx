import { useState, type ChangeEvent, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { toApiError } from "../api/client";
import type { RegisterRequest } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button, ButtonLink } from "../components/Button";
import { ErrorPanel } from "../components/ErrorPanel";
import { Field, TextInput } from "../components/FormField";
import { getStrings, useStrings } from "../i18n";
import { AsideBlock, AuthLayout } from "./AuthLayout";

type Form = { company_name: string; business_id: string; user_name: string; email: string; password: string };
export type RegisterErrors = Partial<Record<keyof Form, string>>;

/** Input validation only (required fields, formats). Exported for tests. */
export function validateRegister(f: Form): RegisterErrors {
  const t = getStrings().auth;
  const e: RegisterErrors = {};
  if (!f.company_name.trim()) e.company_name = t.errRequired(t.companyName);
  if (f.business_id.trim() && !/^\d{7}-\d$/.test(f.business_id.trim())) e.business_id = t.errBusinessId;
  if (!f.user_name.trim()) e.user_name = t.errRequired(t.userName);
  if (!f.email.trim()) e.email = t.errRequired(t.email);
  else if (!/^\S+@\S+\.\S+$/.test(f.email.trim())) e.email = t.errEmail;
  if (!f.password) e.password = t.errRequired(t.password);
  else if (f.password.length < 8) e.password = t.errPassword;
  return e;
}

const FIELDS: (keyof Form)[] = ["company_name", "business_id", "user_name", "email", "password"];

export default function RegisterPage() {
  const strings = useStrings();
  const t = strings.auth;
  const l = strings.login;
  const { register, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState<Form>({ company_name: "", business_id: "", user_name: "", email: "", password: "" });
  const [errors, setErrors] = useState<RegisterErrors>({});
  const [apiError, setApiError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (isAuthenticated && !busy) return <Navigate to="/" replace />;

  const set = (k: keyof Form) => (e: ChangeEvent<HTMLInputElement>) => {
    setForm((f) => ({ ...f, [k]: e.target.value }));
    if (errors[k]) setErrors((x) => ({ ...x, [k]: undefined }));
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setApiError(null);
    const v = validateRegister(form);
    setErrors(v);
    if (Object.keys(v).length) return;
    setBusy(true);
    const body: RegisterRequest = {
      company_name: form.company_name.trim(),
      user_name: form.user_name.trim(),
      email: form.email.trim(),
      password: form.password,
      ...(form.business_id.trim() ? { business_id: form.business_id.trim() } : {}),
    };
    try {
      await register(body);
      navigate("/", { replace: true });
    } catch (err) {
      const ae = toApiError(err);
      setApiError(err);
      // Point the inline message at the offending field when the API tells us which one.
      if (ae.code === "EMAIL_TAKEN") setErrors({ email: ae.message });
      else if (ae.code === "VALIDATION_ERROR" && typeof ae.details.field === "string" && FIELDS.includes(ae.details.field as keyof Form)) {
        setErrors({ [ae.details.field as keyof Form]: typeof ae.details.field_message === "string" ? ae.details.field_message : ae.message });
      }
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
      <AsideBlock title={l.alreadyTitle}>
        <p>{l.alreadyBody}</p>
        <ButtonLink to="/login" variant="outline" size="lg" className="mt-8 w-full">
          {t.backToLogin}
        </ButtonLink>
      </AsideBlock>
    </>
  );

  return (
    <AuthLayout aside={aside}>
      <h1 className="font-display text-[40px] leading-[44px] tracking-[0.01em] text-navy">{l.registerWelcome}</h1>
      <p className="mt-6 text-[18px] font-bold leading-[22px] text-navy">{l.registerLead}</p>
      <form onSubmit={submit} noValidate className="mt-8 space-y-6">
        <Field label={t.companyName} error={errors.company_name}>
          {(p) => <TextInput {...p} autoComplete="organization" value={form.company_name} onChange={set("company_name")} />}
        </Field>
        <Field label={t.businessId} optional hint={t.businessIdHint} error={errors.business_id}>
          {(p) => <TextInput {...p} className="font-mono-code" placeholder="1234567-8" value={form.business_id} onChange={set("business_id")} />}
        </Field>
        <Field label={t.userName} error={errors.user_name}>
          {(p) => <TextInput {...p} autoComplete="name" value={form.user_name} onChange={set("user_name")} />}
        </Field>
        <Field label={t.email} error={errors.email}>
          {(p) => <TextInput {...p} type="email" inputMode="email" autoComplete="email" value={form.email} onChange={set("email")} />}
        </Field>
        <Field label={t.password} hint={t.passwordHint} error={errors.password}>
          {(p) => <TextInput {...p} type="password" autoComplete="new-password" value={form.password} onChange={set("password")} />}
        </Field>
        {apiError != null && <ErrorPanel error={apiError} />}
        <Button type="submit" variant="lime" size="lg" loading={busy}>
          {t.registerCta}
        </Button>
      </form>
    </AuthLayout>
  );
}
