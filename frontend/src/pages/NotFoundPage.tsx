import { Link } from "react-router-dom";
import { Card, PageHeader } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

export default function NotFoundPage() {
  return (
    <div>
      <PageHeader title="Page not found" />
      <Card>
        <EmptyState
          title="This page does not exist."
          action={
            <Link to="/" className="text-sm font-semibold text-primary hover:underline">
              Go to dashboard
            </Link>
          }
        />
      </Card>
    </div>
  );
}
