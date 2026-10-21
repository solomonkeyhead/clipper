import { ImagePlus, Loader2, Type, X } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { toast } from "sonner";
import { brandUrl, uploadBrand, type BrandKit } from "@/api/client";
import { cn } from "@/lib/utils";
import { Button } from "./ui";

/* The brand kit (D240): a campaign's own caption font and colours and a small logo, with a preview of the
   frame drawn to the same numbers the render uses (render/graph.py logo_box, the shapes' safe areas). */

/** Clipper's own caption look (bold_pop): white, black outline, the spoken word yellow. */
const OWN = { text: "#ffffff", highlight: "#ffd600", outline: "#000000" };

/** Per shape: [width/height, caption bottom, logo box l t r b, logo max w h], as fractions of the frame. */
const GEOMETRY: Record<string, { ratio: number; captions: number; box: [number, number, number, number]; logo: [number, number] }> = {
  "9:16": { ratio: 9 / 16, captions: 1 - 680 / 1920, box: [120 / 1080, 300 / 1920, 960 / 1080, 820 / 1920], logo: [180 / 1080, 120 / 1920] },
  "1:1": { ratio: 1, captions: 1 - 252 / 1080, box: [120 / 1080, 60 / 1080, 960 / 1080, 400 / 1080], logo: [180 / 1080, 120 / 1080] },
  "16:9": { ratio: 16 / 9, captions: 1 - 80 / 1080, box: [96 / 1920, 60 / 1080, 1824 / 1920, 1000 / 1080], logo: [180 / 1920, 120 / 1080] },
};

export const autoCorner = (shape: string) => (shape === "16:9" ? "top_right" : "bottom_right");

/** The uploaded font, loaded into the page under its file name so previews can use it. */
export function useBrandFont(file: string | undefined): string | null {
  const [ready, setReady] = useState<string | null>(null);
  useEffect(() => {
    if (!file) { setReady(null); return; }
    const family = `brand-${file.split(".")[0]}`;
    if ([...document.fonts].some((f) => f.family === family)) { setReady(family); return; }
    const face = new FontFace(family, `url(${brandUrl(file)})`);
    face.load().then((f) => { document.fonts.add(f); setReady(family); }).catch(() => setReady(null));
  }, [file]);
  return ready;
}

/** The caption look as CSS: the kit's font and colours over Clipper's own. */
export function captionCss(kit: Partial<BrandKit> | undefined, family: string | null): CSSProperties {
  const outline = kit?.outline_colour || OWN.outline;
  return {
    fontFamily: family ? `"${family}", var(--font-display)` : "var(--font-display)",
    color: kit?.text_colour || OWN.text,
    textShadow: [[-1, -1], [1, -1], [-1, 1], [1, 1], [0, 2]].map(([x, y]) => `${x * 0.06}em ${y * 0.06}em 0 ${outline}`).join(","),
    fontWeight: family ? 400 : 800,
  };
}

/** A frame in the campaign's shape with a caption, the hook and the logo where the render puts them. */
export function BrandPreview({ shape, kit, hook = "The hook line goes here", className }: {
  shape: string; kit: Partial<BrandKit>; hook?: string; className?: string;
}) {
  const g = GEOMETRY[shape] ?? GEOMETRY["9:16"];
  const family = useBrandFont(kit.font || undefined);
  const corner = !kit.logo_corner || kit.logo_corner === "auto" ? autoCorner(shape) : kit.logo_corner;
  const [l, t, r, b] = g.box;
  // Type is sized by the frame's short side, as the render does: 96 px captions on 1080.
  const unit = g.ratio > 1 ? `${100 / g.ratio}cqw` : "100cqw";
  const css = captionCss(kit, family);
  return (
    <div data-testid="brand-preview" aria-label={`Preview of a ${shape} clip`} role="img"
         className={cn("relative overflow-hidden rounded-md bg-[linear-gradient(160deg,#3a4a5c,#1c232b_60%,#2b1f1a)] ring-1 ring-line [container-type:inline-size]", className)}
         style={{ aspectRatio: String(g.ratio) }}>
      <div className="absolute inset-x-[8%] text-center uppercase leading-[1.1]"
           style={{ ...css, top: `${t * 100}%`, fontSize: `calc(${unit} * 0.064)` }}>{hook}</div>
      <div className="absolute inset-x-0 text-center leading-[1.15]"
           style={{ ...css, bottom: `${(1 - g.captions) * 100}%`, fontSize: `calc(${unit} * 0.089)` }}>
        Every caption <span style={{ color: kit.highlight_colour || OWN.highlight }}>looks</span><br />like this
      </div>
      {kit.logo && (
        <img src={brandUrl(kit.logo)} alt="Logo" className="absolute object-contain"
             style={{ maxWidth: `${g.logo[0] * 100}%`, maxHeight: `${g.logo[1] * 100}%`,
                      ...(corner.endsWith("left") ? { left: `${l * 100}%` } : { right: `${(1 - r) * 100}%` }),
                      ...(corner.startsWith("top") ? { top: `${t * 100}%` } : { bottom: `${(1 - b) * 100}%` }) }} />
      )}
    </div>
  );
}

function Colour({ label, value, own, onChange }: { label: string; value: string; own: string; onChange: (v: string) => void }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="color" aria-label={label} value={value || own} onChange={(e) => onChange(e.target.value)}
             className="h-8 w-10 cursor-pointer rounded-sm border border-line bg-surface-1 p-0.5" />
      <span className="flex flex-col leading-tight">
        {label}
        {value ? <button type="button" className="text-left text-xs text-muted hover:text-fg" onClick={() => onChange("")}>Use Clipper's own</button>
          : <span className="text-xs text-subtle">Clipper's own</span>}
      </span>
    </label>
  );
}

function Upload({ kind, label, accept, has, onDone, onClear }: {
  kind: "font" | "logo"; label: string; accept: string; has: string; onDone: (r: { file: string; family?: string }) => void; onClear: () => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const pick = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    try {
      onDone(await uploadBrand(kind, file));
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-2">
      <input ref={input} type="file" accept={accept} className="hidden" aria-label={label} onChange={(e) => pick(e.target.files?.[0])} />
      <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={() => input.current?.click()}>
        {busy ? <Loader2 className="size-3.5 animate-spin" /> : kind === "font" ? <Type className="size-3.5" /> : <ImagePlus className="size-3.5" />}
        {label}
      </Button>
      {has && <Button type="button" size="sm" variant="ghost" onClick={onClear}><X className="size-3.5" /> Remove</Button>}
    </div>
  );
}

/** The campaign editor's Brand section: every field is optional, and empty is Clipper's own look. */
export function BrandFields({ shape, kit, onChange }: { shape: string; kit: BrandKit; onChange: (kit: BrandKit) => void }) {
  const set = (change: Partial<BrandKit>) => onChange({ ...kit, ...change });
  const [family, setFamily] = useState("");
  const auto = autoCorner(shape).replace("_", " ");
  return (
    <div className="grid gap-5 sm:grid-cols-[minmax(0,1fr)_200px]">
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">Caption font <span className="ml-1 text-xs font-normal text-subtle">optional</span></span>
          <Upload kind="font" label={kit.font ? "Change the font" : "Upload a font"} accept=".ttf,.otf" has={kit.font}
                  onDone={(r) => { set({ font: r.file }); setFamily(r.family ?? ""); }} onClear={() => { set({ font: "" }); setFamily(""); }} />
          <p className="text-xs text-muted">{kit.font ? `${family || "Your font"} for captions and the hook. ` : "Clipper's own heavy font until you upload one. "}A .ttf or .otf file, up to 8 MB.</p>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">Caption colours</span>
          <div className="flex flex-wrap gap-x-5 gap-y-2">
            <Colour label="Text" value={kit.text_colour} own={OWN.text} onChange={(v) => set({ text_colour: v })} />
            <Colour label="Word being said" value={kit.highlight_colour} own={OWN.highlight} onChange={(v) => set({ highlight_colour: v })} />
            <Colour label="Outline" value={kit.outline_colour} own={OWN.outline} onChange={(v) => set({ outline_colour: v })} />
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-medium">Logo <span className="ml-1 text-xs font-normal text-subtle">optional</span></span>
          <Upload kind="logo" label={kit.logo ? "Change the logo" : "Upload a logo"} accept=".png,.jpg,.jpeg,image/png,image/jpeg" has={kit.logo}
                  onDone={(r) => set({ logo: r.file })} onClear={() => set({ logo: "" })} />
          {kit.logo && (
            <select aria-label="Logo corner" value={kit.logo_corner} onChange={(e) => set({ logo_corner: e.target.value as BrandKit["logo_corner"] })}
                    className="h-9 w-fit rounded-sm border border-line bg-surface-2 px-3 text-sm">
              <option value="auto">Automatic ({auto})</option>
              <option value="top_left">Top left</option><option value="top_right">Top right</option>
              <option value="bottom_left">Bottom left</option><option value="bottom_right">Bottom right</option>
            </select>
          )}
          <p className="text-xs text-muted">A PNG or JPG, up to 5 MB. Shown small in a corner, clear of the captions and the apps' buttons.</p>
        </div>
      </div>
      <BrandPreview shape={shape} kit={kit} className="w-full self-start" />
    </div>
  );
}
