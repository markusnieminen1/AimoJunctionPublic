import { Link } from "react-router-dom";
import { useAgents } from "../api/hooks";
import { useStrings } from "../i18n";
import { buttonClass } from "./Button";

/**
 * Portal "info band" (DESIGN_VALIO §4.10, "Hello new user!"): petrol band, 18 px white text, lime CTA on the right.
 * "Create your first purchasing agent" → /agents?create=1. Full-bleed by default; pass `boxed` inside a padded page.
 */
export function OnboardingBanner({ className = "", boxed = false }: { className?: string; boxed?: boolean }) {
  const t = useStrings().front;
  return (
    <section className={`bg-navy text-white ${boxed ? "rounded" : ""} ${className}`} aria-label={t.onboardingTitle} data-testid="onboarding-banner">
      <div className={`${boxed ? "px-6" : "container-portal"} flex min-h-[144px] flex-col justify-center gap-4 py-8 md:flex-row md:items-center md:justify-between`}>
        <div className="min-w-0">
          <h2 className="font-display text-[32px] leading-[35px] tracking-[0.01em] text-white">{t.onboardingTitle}</h2>
          <p className="mt-1 text-[18px] leading-[27px] text-white">{t.onboardingBody}</p>
        </div>
        <Link to="/agents?create=1" className={buttonClass("lime", "lg", "shrink-0 self-start md:self-auto")}>
          {t.onboardingCta}
        </Link>
      </div>
    </section>
  );
}

/** Renders the onboarding band only when the company has 0 agents (users only; silent on errors). */
export function OnboardingBannerIfNoAgents({ className = "", boxed = false }: { className?: string; boxed?: boolean }) {
  const agents = useAgents({ silent: true });
  if (!agents.data || agents.data.total > 0 || agents.data.items.length > 0) return null;
  return <OnboardingBanner className={className} boxed={boxed} />;
}
