// Portal table classes (DESIGN_VALIO §4.8): rows, not gridlines. Wrap in `tableWrapClass`; head row petrol-light bold 14;
// body rows white 14 px with a #DEE4E6 bottom border and #F9FBFC hover; numbers right-aligned + tabular.
// Below 768 px prefer stacked cards (label / value pairs) over horizontal scroll.

export const tableWrapClass = "overflow-x-auto rounded border border-line bg-white";
export const tableClass = "w-full border-collapse text-left text-[14px]";
export const theadClass = "bg-primary-soft";
export const thClass = "px-4 py-3 text-[14px] font-bold leading-[17px] text-ink";
export const thNumClass = `${thClass} text-right`;
export const trClass = "border-b border-line last:border-b-0 hover:bg-[#F9FBFC]";
export const tdClass = "px-4 py-3 align-top text-[14px] leading-5 text-ink";
export const tdNumClass = `${tdClass} tabular-nums text-right whitespace-nowrap`;
