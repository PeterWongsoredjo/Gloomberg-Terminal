import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface EmptyPanelStateProps {
  title: string;
}

/** The explicit no-data state every dense component falls back to — never a bare value. */
export function EmptyPanelState({ title }: EmptyPanelStateProps) {
  return (
    <Card className="flex-1">
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        No data — freshness unknown, not yet wired to the serving API.
      </CardContent>
    </Card>
  );
}
