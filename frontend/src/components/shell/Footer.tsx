// Solid petrol footer (DESIGN_VALIO §3.7) with our own columns, the "not an official Valio Aimo service" disclaimer and the
// server clock (useServerNow). No Valio links, phone numbers or social icons.
import { Link } from "react-router-dom";
import { useStrings } from "../../i18n";
import { formatDate, formatTime, helsinkiDate } from "../../lib/dates";
import { useServerNow } from "../../lib/useServerNow";
import { AimoWordmark } from "../AimoWordmark";
import { JunctionEditionBadge } from "../JunctionEditionBadge";

type Col = { title: string; links: { label: string; to?: string; href?: string }[] };

function ServerClock() {
  const f = useStrings().portal.footer;
  const now = useServerNow(15_000);
  const iso = new Date(now).toISOString();
  return (
    <span className="tabular-nums" data-testid="server-clock">
      {f.serverTime} {formatDate(helsinkiDate(now))} {formatTime(iso)}
    </span>
  );
}

export function Footer({ signedIn = true }: { signedIn?: boolean }) {
  const f = useStrings().portal.footer;
  const cols: Col[] = signedIn
    ? [
        {
          title: f.onlineStore,
          links: [
            { label: f.products, to: "/catalog" },
            { label: f.cartQuote, to: "/cart" },
            { label: f.orderManagement, to: "/orders" },
            { label: f.approvals, to: "/approvals" },
          ],
        },
        {
          title: f.myAimo,
          links: [
            { label: f.overview, to: "/" },
            { label: f.assistant, to: "/assistant" },
            { label: f.agents, to: "/agents" },
            { label: f.audit, to: "/audit" },
          ],
        },
        {
          title: f.developers,
          links: [
            { label: f.apiDocs, href: "/api/docs" },
            { label: f.openapi, href: "/api/openapi.json" },
            { label: f.mcp, href: "/api/mcp" },
          ],
        },
        { title: f.brand, links: [{ label: f.brandLine1 }, { label: f.brandLine2 }] },
      ]
    : [
        { title: f.developers, links: [{ label: f.apiDocs, href: "/api/docs" }, { label: f.openapi, href: "/api/openapi.json" }] },
        { title: f.brand, links: [{ label: f.brandLine1 }, { label: f.brandLine2 }] },
      ];
  const link = "text-[14px] leading-[17px] text-primary-soft hover:underline";
  return (
    <footer className="bg-navy text-primary-soft" data-testid="footer">
      <div className="container-portal pb-8 pt-10">
        <div className="grid grid-cols-2 gap-x-6 gap-y-8 lg:grid-cols-[180px_repeat(4,minmax(0,1fr))]">
          <div className="col-span-2 lg:col-span-1">
            <AimoWordmark tone="white" />
            <div className="mt-3">
              <JunctionEditionBadge size="md" testId="junction-edition-footer" />
            </div>
          </div>
          {cols.map((c) => (
            <div key={c.title}>
              <h2 className="text-[14px] font-bold leading-[17px] text-primary-soft">{c.title}</h2>
              <ul className="mt-4 space-y-4">
                {c.links.map((l) => (
                  <li key={l.label}>
                    {l.to ? (
                      <Link to={l.to} className={link}>
                        {l.label}
                      </Link>
                    ) : l.href ? (
                      <a href={l.href} className={link}>
                        {l.label}
                      </a>
                    ) : (
                      <span className="text-[14px] leading-[17px]">{l.label}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="mt-12 flex flex-col gap-3 text-[14px] leading-[17px] md:flex-row md:items-center md:justify-between">
          <span data-testid="footer-disclaimer">{f.disclaimer}</span>
          <ServerClock />
        </div>
      </div>
    </footer>
  );
}
