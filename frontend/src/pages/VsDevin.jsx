/**
 * VsDevin.jsx — thin wrapper since Iter 358.
 *
 * All comparison content moved to src/data/competitors.js and the
 * generic shell to src/pages/VsPage.jsx (single source shared by every
 * /vs/* page, the /compare hub and the build-time SEO snapshots).
 * This file survives only so the existing lazy route import keeps
 * working.
 */
import React from "react";
import VsPage from "./VsPage";

export default function VsDevin() {
  return <VsPage forcedSlug="devin" />;
}
