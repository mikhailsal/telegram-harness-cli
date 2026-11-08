import { Link } from "../Link.tsx";

export function Home() {
  return (
    <>
      <h1>Welcome</h1>
      <p>Tell us what works and what does not. Every message is read.</p>
      <p>
        <Link to="/feedback">Send feedback</Link>
      </p>
    </>
  );
}
