import { useState, useCallback } from "react";

export interface RepoState {
  repoPath: string;
  outputPath: string;
  setRepoPath: (v: string) => void;
  setOutputPath: (v: string) => void;
}

export function useRepo(): RepoState {
  const [repoPath, setRepoPath] = useState(() => localStorage.getItem("jidra_repo") ?? "");
  const [outputPath, setOutputPath] = useState(() => localStorage.getItem("jidra_output") ?? "");

  const saveRepo = useCallback((v: string) => {
    setRepoPath(v);
    localStorage.setItem("jidra_repo", v);
  }, []);

  const saveOutput = useCallback((v: string) => {
    setOutputPath(v);
    localStorage.setItem("jidra_output", v);
  }, []);

  return { repoPath, outputPath, setRepoPath: saveRepo, setOutputPath: saveOutput };
}
