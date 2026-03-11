import { Bot, Hammer, User2, Wrench } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { formatDateTime } from "@/lib/message";
import type { MessagePart, MessageSchema } from "@/types";

function PartView({ part }: { part: MessagePart }) {
  if (part.type === "text") {
    return <p className="whitespace-pre-wrap leading-7">{part.text}</p>;
  }

  if (part.type === "image_url") {
    return (
      <img
        src={part.image_url}
        alt="message asset"
        className="mt-2 max-h-80 rounded-2xl border border-border object-cover"
      />
    );
  }

  if (part.type === "tool_call") {
    return (
      <div className="rounded-2xl bg-slate-900/5 px-4 py-3 text-sm">
        <div className="mb-2 flex items-center gap-2 font-medium text-slate-800">
          <Hammer className="h-4 w-4" />
          调用工具 {part.name}
        </div>
        <pre className="overflow-x-auto whitespace-pre-wrap text-xs text-muted-foreground">
          {JSON.stringify(part.args, null, 2)}
        </pre>
      </div>
    );
  }

  return (
    <div className="rounded-2xl bg-amber-50 px-4 py-3 text-sm text-amber-950">
      <div className="mb-2 flex items-center gap-2 font-medium">
        <Wrench className="h-4 w-4" />
        工具结果 {part.name}
      </div>
      <pre className="overflow-x-auto whitespace-pre-wrap text-xs leading-6">
        {part.content}
      </pre>
    </div>
  );
}

export function MessageBubble({ message }: { message: MessageSchema }) {
  const isUser = message.role === "user";
  const isTool = message.role === "tool";

  return (
    <div className={cn("flex w-full", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[85%] space-y-3 rounded-[1.5rem] px-4 py-4 shadow-sm sm:max-w-[70%]",
          isUser
            ? "bg-primary text-primary-foreground"
            : isTool
              ? "bg-amber-100 text-amber-950"
              : "glass-panel"
        )}
      >
        <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.18em]">
          {isUser ? <User2 className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
          <span>{message.role}</span>
          {message.timestamp ? (
            <Badge
              variant={isUser ? "accent" : "muted"}
              className="ml-auto border-none bg-white/15 text-current"
            >
              {formatDateTime(message.timestamp)}
            </Badge>
          ) : null}
        </div>
        <div className="space-y-3 text-sm">
          {message.parts.map((part, index) => (
            <PartView key={`${part.type}-${index}`} part={part} />
          ))}
        </div>
      </div>
    </div>
  );
}
