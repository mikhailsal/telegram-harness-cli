import { z } from "zod";

export const idSchema = z.union([z.string(), z.number()]);
export const threadResult = z
  .object({ thread: z.object({ id: z.string() }).loose() })
  .loose();
export const turnResult = z
  .object({ turn: z.object({ id: z.string() }).loose() })
  .loose();
export const contentSchema = z
  .object({ type: z.string(), text: z.string().optional() })
  .loose();
export const itemSchema = z
  .object({
    id: z.string(),
    type: z.string(),
    clientId: z.string().nullish(),
    text: z.string().optional(),
    content: z.array(contentSchema).optional(),
  })
  .loose();
export const turnSchema = z
  .object({ id: z.string(), status: z.string(), items: z.array(itemSchema) })
  .loose();
export const turnList = z
  .object({ data: z.array(turnSchema), nextCursor: z.string().nullish() })
  .loose();
export const notification = z
  .object({
    method: z.string(),
    id: idSchema.optional(),
    params: z
      .object({
        threadId: z.string(),
        turnId: z.string().optional(),
        turn: turnSchema.optional(),
        item: itemSchema.optional(),
        requestId: idSchema.optional(),
      })
      .loose(),
  })
  .loose();
export type Notification = z.infer<typeof notification>;
export type Item = z.infer<typeof itemSchema>;
export type Turn = z.infer<typeof turnSchema>;

export const commandResult = z
  .object({ exitCode: z.number(), stdout: z.string(), stderr: z.string() })
  .loose();

export const questionResponse = z.object({
  answers: z.record(z.string(), z.object({ answers: z.array(z.string()) })),
});

export const pendingStatus = z.object({
  thread: z.object({
    status: z.discriminatedUnion("type", [
      z.object({
        type: z.literal("active"),
        activeFlags: z.array(
          z.enum(["waitingOnApproval", "waitingOnUserInput"]),
        ),
      }),
      z.object({ type: z.literal("idle") }),
      z.object({ type: z.literal("notLoaded") }),
      z.object({ type: z.literal("systemError") }),
    ]),
  }),
});
