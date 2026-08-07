"use client";

import { ExternalLink } from "lucide-react";

import { LINK_CLASS } from "@/lib/palette";

/* The one way out of the terminal to a source page. */

interface LinkOutProps {
  href: string | null;
  label?: string;
  variant?: "button" | "inline";
}

const BUTTON_CLASS =
  "inline-flex items-center gap-1.5 border border-zinc-700 bg-zinc-900 px-2 py-0.5 tracking-wide text-zinc-100 transition-colors hover:border-[#4DA6FF] hover:text-[#4DA6FF]";

export function LinkOut({ href, label, variant = "inline" }: LinkOutProps) {
  if (href === null) return null;
  const inline = variant === "inline";
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className={inline ? `text-[10px] ${LINK_CLASS} hover:underline` : BUTTON_CLASS}
    >
      {!inline && <ExternalLink className="h-3 w-3" />}
      {label ?? href}
    </a>
  );
}
