import type { InternalHistoryInput } from "./messages.js";
import { ResourceStore } from "./resources.js";
import type { TurnInput } from "./types.js";

export interface QuestionAnswer {
  /** The tapped button's callback update, still queued in the inbox. */
  updateId: number;
  /** The meta row that holds the question and, once answered, its answer. */
  row: string;
  /** The row's value before the tap; a different value means a lost race. */
  expected: string;
  answered: string;
  turn: TurnInput;
  /** The text Codex receives as the owner's answer. */
  reply: string;
  history: Omit<InternalHistoryInput, "turnId">;
}
const replyKey = (turnId: string): string => `question-reply/${turnId}`;

// A button answer to a Codex question becomes a turn of its own: the tap's
// inbox update is assigned to it, and its text is the stored reply.
export class QuestionStore extends ResourceStore {
  answerQuestion(input: QuestionAnswer): boolean {
    return this.atomic(() => {
      const inbox = this.getInbox(input.updateId);
      if (
        inbox?.status !== "queued" ||
        inbox.entityId !== input.turn.entityId ||
        this.getCursor(input.row) !== input.expected
      )
        return false;
      this.setCursor(input.row, input.answered);
      this.createTurn(input.turn);
      if (!this.assignInbox(input.updateId, input.turn.id))
        throw new Error("Inbox assignment changed during transaction");
      this.setCursor(replyKey(input.turn.id), input.reply);
      if (
        !this.insertInternalMessage({ ...input.history, turnId: input.turn.id })
      )
        throw new Error("Answer history already exists");
      return true;
    });
  }
  /** The answer text of a turn created by a button tap. */
  questionReply(turnId: string): string | undefined {
    return this.getCursor(replyKey(turnId));
  }
}
