import { z } from "zod";

export const userSchema = z.object({ id: z.string().uuid(), email: z.string().email() });
export const authSchema = z.object({
  access_token: z.string().min(1),
  token_type: z.literal("bearer"),
  user: userSchema
});
export const providersSchema = z.object({ local: z.boolean(), oidc: z.boolean() });
export const runSchema = z.object({
  id: z.string().uuid(),
  status: z.enum(["pending", "running", "completed", "failed"]),
  answer: z.string().nullable(),
  error: z.string().nullable(),
  steps: z.number().int().nonnegative()
});

export type User = z.infer<typeof userSchema>;
export type AuthSession = z.infer<typeof authSchema>;
export type AgentRun = z.infer<typeof runSchema>;
