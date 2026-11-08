import { Link } from "../Link.tsx";

export function NotFound() {
  return (
    <>
      <h1>Page not found</h1>
      <p>There is no page at this address.</p>
      <p>
        <Link to="/">Go to the home page</Link>
      </p>
    </>
  );
}
