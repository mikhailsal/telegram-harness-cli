import { useEffect } from "react";

import styles from "./App.module.css";
import { Link } from "./Link.tsx";
import logo from "./logo.svg";
import { Feedback } from "./pages/Feedback.tsx";
import { Home } from "./pages/Home.tsx";
import { NotFound } from "./pages/NotFound.tsx";
import { usePath } from "./route.ts";

const PAGES = [
  { path: "/", label: "Home", title: "Home", view: Home },
  {
    path: "/feedback",
    label: "Feedback",
    title: "Send feedback",
    view: Feedback,
  },
];
const MISSING = { title: "Page not found", view: NotFound };

export function App() {
  const path = usePath();
  const page = PAGES.find((candidate) => candidate.path === path) ?? MISSING;
  useEffect(() => {
    document.title = `${page.title} · Feedback desk`;
  }, [page]);
  return (
    <div className={styles.app}>
      <header className={styles.header}>
        <span className={styles.name}>
          <img src={logo} alt="" width="32" height="32" />
          Feedback desk
        </span>
        <nav aria-label="Main">
          <ul className={styles.links}>
            {PAGES.map((link) => (
              <li key={link.path}>
                <Link to={link.path}>{link.label}</Link>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main>
        <page.view />
      </main>
    </div>
  );
}
