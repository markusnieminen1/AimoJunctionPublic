// Portal app shell (DESIGN_VALIO §3): sticky header = 40 px petrol store-tab bar + 80 px header row (56 px on < 992),
// variant per route (webshop lavender / My Aimo petrol-light), mobile drawer, solid petrol footer.
// No bottom thumb-bar: on phones the sticky header's Bell + red count badge is one tap to /approvals.
import { useEffect, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { Drawer } from "./shell/Drawer";
import { Footer } from "./shell/Footer";
import { DesktopHeaderRow, MobileHeaderRow } from "./shell/HeaderRow";
import { TopBar } from "./shell/TopBar";
import { headerVariantFor, isFullBleed } from "./shell/shared";

export { headerVariantFor } from "./shell/shared";

export function AppShell() {
  const loc = useLocation();
  const variant = headerVariantFor(loc.pathname);
  const [drawer, setDrawer] = useState(false);
  const [search, setSearch] = useState(false);
  useEffect(() => {
    setDrawer(false);
    setSearch(false);
    // A new page starts at the top (SPA navigation keeps the old scroll position otherwise: tapping the Approvals
    // bell from the footer of a long page would land below the approval card).
    if (!loc.hash) window.scrollTo?.(0, 0);
  }, [loc.pathname, loc.hash]);

  return (
    <div className="flex min-h-screen flex-col bg-white">
      <header className="sticky top-0 z-30" data-testid="site-header">
        <TopBar variant={variant} />
        <DesktopHeaderRow variant={variant} />
        <MobileHeaderRow variant={variant} onMenu={() => setDrawer(true)} searchOpen={search} onToggleSearch={() => setSearch((s) => !s)} />
      </header>
      <main className="flex-1">
        {isFullBleed(loc.pathname) ? (
          <Outlet />
        ) : (
          <div className="container-portal pb-16 pt-6 md:pt-8 lg:pt-12">
            <Outlet />
          </div>
        )}
      </main>
      <Footer />
      {drawer && <Drawer onClose={() => setDrawer(false)} />}
    </div>
  );
}
