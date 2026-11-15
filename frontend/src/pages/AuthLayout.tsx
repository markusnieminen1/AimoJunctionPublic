import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { AimoWordmark } from "../components/AimoWordmark";
import { Footer } from "../components/shell/Footer";
import { TopBar } from "../components/shell/TopBar";

// Portal login page model (DESIGN_VALIO §6 /login, login_1280.png): petrol store-tab bar, 80 px petrol-light logo row,
// a petrol hero band (subtle diagonal pattern instead of a photo) holding a centred 2-panel card (white form 440 px +
// petrol-light info 328 px), then the footer. On phones the panels stack full width without hero padding.
export function AuthLayout({ children, aside }: { children: ReactNode; aside: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-white">
      <header>
        <TopBar variant="myaimo" signedIn={false} />
        <div className="hidden h-20 bg-primary-soft md:block">
          <div className="container-portal flex h-20 items-center">
            <Link to="/login" aria-label="Aimo Annos">
              <AimoWordmark />
            </Link>
          </div>
        </div>
      </header>
      <main className="flex-1 md:band-petrol-pattern md:py-14">
        <div className="mx-auto flex w-full max-w-[768px] flex-col md:flex-row">
          <section className="min-w-0 bg-white px-8 py-10 md:w-[440px] md:shrink-0 md:p-12">{children}</section>
          <aside className="bg-primary-soft px-8 py-10 md:min-w-0 md:flex-1 md:p-12">{aside}</aside>
        </div>
      </main>
      <Footer signedIn={false} />
    </div>
  );
}

/** Right-panel heading (h4 sans 700 20 petrol) + 14 px text, like "Mikä on Minun Aimo?". */
export function AsideBlock({ title, children }: { title: ReactNode; children: ReactNode }) {
  return (
    <div className="mb-8 last:mb-0">
      <h2 className="text-[20px] font-bold leading-[22px] text-navy">{title}</h2>
      <div className="mt-4 text-[14px] leading-[17px] text-ink">{children}</div>
    </div>
  );
}
