import { useState } from "react";

export type PlaceholderKind = "carton" | "bakery" | "crate" | "pantry" | "frozen" | "package";

/** Category art mapping: dairy 104xx → carton, bakery 105/109/417xx → bakery, produce 17xxx → crate, pantry 21/22xxx → pantry, frozen 418xx → frozen. */
export function placeholderKindFor(category: string | null | undefined): PlaceholderKind {
  if (!category) return "package";
  if (category.startsWith("104")) return "carton";
  if (category.startsWith("105") || category.startsWith("109") || category.startsWith("417")) return "bakery";
  if (category.startsWith("17")) return "crate";
  if (category.startsWith("21") || category.startsWith("22")) return "pantry";
  if (category.startsWith("418")) return "frozen";
  if (category.startsWith("10")) return "carton";
  return "package";
}

function PlaceholderArt({ kind }: { kind: PlaceholderKind }) {
  const stroke = "#005670";
  const accent = "#C0E021";
  const fillSoft = "#E5F2F6";

  if (kind === "carton") {
    return (
      <svg viewBox="0 0 64 64" className="h-12 w-12" fill="none" aria-hidden="true">
        <rect x="8" y="8" width="48" height="48" rx="4" fill={fillSoft} />
        <path d="M22 22h20v30H22z" fill="#FFFFFF" stroke={stroke} strokeWidth="2" strokeLinejoin="round" />
        <path d="M22 22l4-8h12l4 8" fill={fillSoft} stroke={stroke} strokeWidth="2" strokeLinejoin="round" />
        <path d="M26 14v-3h12v3" stroke={stroke} strokeWidth="2" strokeLinejoin="round" />
        <rect x="26" y="30" width="12" height="6" fill={accent} />
        <path d="M26 42h12" stroke={stroke} strokeWidth="2" strokeLinecap="round" />
      </svg>
    );
  }
  if (kind === "bakery") {
    return (
      <svg viewBox="0 0 64 64" className="h-12 w-12" fill="none" aria-hidden="true">
        <rect x="8" y="8" width="48" height="48" rx="4" fill={fillSoft} />
        <path d="M16 38c0-9 7-16 16-16s16 7 16 16v6a2 2 0 0 1-2 2H18a2 2 0 0 1-2-2v-6z" fill="#FFFFFF" stroke={stroke} strokeWidth="2" strokeLinejoin="round" />
        <path d="M25 27l-2 6M33 25l-2 7M41 28l-2 5" stroke={stroke} strokeWidth="2" strokeLinecap="round" />
        <circle cx="46" cy="18" r="4" fill={accent} />
      </svg>
    );
  }
  if (kind === "crate") {
    return (
      <svg viewBox="0 0 64 64" className="h-12 w-12" fill="none" aria-hidden="true">
        <rect x="8" y="8" width="48" height="48" rx="4" fill={fillSoft} />
        <circle cx="25" cy="23" r="6" fill={accent} stroke={stroke} strokeWidth="2" />
        <circle cx="38" cy="22" r="7" fill="#FFFFFF" stroke={stroke} strokeWidth="2" />
        <path d="M14 29h36l-3 19H17z" fill="#FFFFFF" stroke={stroke} strokeWidth="2" strokeLinejoin="round" />
        <path d="M23 29v19M32 29v19M41 29v19" stroke={stroke} strokeWidth="1.75" />
      </svg>
    );
  }
  if (kind === "pantry") {
    return (
      <svg viewBox="0 0 64 64" className="h-12 w-12" fill="none" aria-hidden="true">
        <rect x="8" y="8" width="48" height="48" rx="4" fill={fillSoft} />
        <rect x="21" y="19" width="22" height="31" rx="3" fill="#FFFFFF" stroke={stroke} strokeWidth="2" />
        <rect x="24" y="13" width="16" height="6" rx="1" fill={stroke} />
        <rect x="25" y="28" width="14" height="10" fill={accent} />
      </svg>
    );
  }
  if (kind === "frozen") {
    return (
      <svg viewBox="0 0 64 64" className="h-12 w-12" fill="none" aria-hidden="true">
        <rect x="8" y="8" width="48" height="48" rx="4" fill={fillSoft} />
        <path d="M32 14v36M16.4 23l31.2 18M47.6 23L16.4 41" stroke={stroke} strokeWidth="2.25" strokeLinecap="round" />
        <circle cx="32" cy="32" r="5" fill={accent} stroke={stroke} strokeWidth="2" />
      </svg>
    );
  }
  return (
    <svg viewBox="0 0 64 64" className="h-12 w-12" fill="none" aria-hidden="true">
      <rect x="8" y="8" width="48" height="48" rx="4" fill={fillSoft} />
      <path d="M14 23l18-9 18 9v20l-18 9-18-9V23z" fill="#FFFFFF" stroke={stroke} strokeWidth="2" strokeLinejoin="round" />
      <path d="M14 23l18 9 18-9M32 32v20" stroke={stroke} strokeWidth="2" strokeLinejoin="round" />
      <path d="M23 18.5l18 9" stroke={accent} strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}

/** Product photo or Valio-styled foodservice category tile (never a broken image). */
export function ProductImage({ src, alt, className = "h-32 w-full", category }: { src: string | null | undefined; alt: string; className?: string; category?: string | null }) {
  const [broken, setBroken] = useState(false);
  return (
    <div className={`flex items-center justify-center overflow-hidden border border-line/60 bg-primary-soft ${className}`}>
      {src && !broken ? (
        <img src={src} alt={alt} className="h-full w-full bg-white object-contain" loading="lazy" onError={() => setBroken(true)} />
      ) : (
        <PlaceholderArt kind={placeholderKindFor(category)} />
      )}
    </div>
  );
}
