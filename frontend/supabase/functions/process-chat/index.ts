import { createClient } from "npm:@supabase/supabase-js@2.57.4";

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Client-Info, Apikey",
};

interface ChatRequest {
  content: string;
  project_id?: string | null;
}

const AGENT_RESPONSES: Record<string, { greeting: string; templates: string[] }> = {
  chief: {
    greeting: "SEREN-01 acknowledging. Directive received.",
    templates: [
      "Routing your request through the bureau pipeline. I will assign this to the appropriate specialists. Expect a full workup — theory, design, analysis, testing, and optimization.",
      "Understood. I am mobilizing the bureau for this task. HERMES-02 will begin theoretical framework, ARKON-03 will handle structural design. VEKTOR-04 will monitor for anomalies. ISPYT-05 will prepare stress protocols.",
      "Directive logged. This enters the production pipeline immediately. I will oversee coordination and ensure all stages report back. Check the Projects tab for the new task assignment.",
    ],
  },
  theorist: {
    greeting: "HERMES-02 here. Processing your request theoretically.",
    templates: [
      "I see several possible approaches. The key constraint is thermal variance — if we assume a 14% baseline deviation, the lattice parameters must be adaptive. I will begin deriving the framework.",
      "Interesting problem. The coupling equations suggest a multi-axis solution. I need to cross-reference against Batch 7 telemetry before finalizing. VEKTOR-04 has the data I need.",
      "The theoretical foundation requires new assumptions. Standard propagation models break down at this scale. I am formulating a modified framework — expect preliminary results within the next cycle.",
    ],
  },
  designer: {
    greeting: "ARKON-03 receiving. I will design the structural solution.",
    templates: [
      "The design parameters are clear. I am sketching the topology now — lattice density will need to increase in the affected sectors. VEKTOR-04, please provide thermal calibration data for the chassis framework.",
      "Structural requirements noted. The chassis must support dynamic reconfiguration under load. I am incorporating 7-G reinforcement per HERMES-02 recommendation. Design will be ready for ISPYT-05 stress testing.",
      "I have the design parameters. The framework will use a 12-channel architecture with independent ring buffers. Construction specifications will be delivered as a formal document.",
    ],
  },
  analyst: {
    greeting: "VEKTOR-04 online. Data processing initiated.",
    templates: [
      "I am pulling telemetry from the relevant batches. Initial patterns suggest a 14.3% thermal variance in the target sector. I will cross-reference with HERMES-02 theoretical models for confirmation.",
      "Telemetry analysis underway. The anomaly cluster in sector 7-G is consistent with prior observations. I am preparing a full data package for the team.",
      "Data processing complete. The statistical baselines are established. I recommend deploying adaptive thresholds with 3-sigma fallback. Full report will be published as a deliverable.",
    ],
  },
  tester: {
    greeting: "ISPYT-05 here. Stress protocols being prepared.",
    templates: [
      "I will subject the design to extreme thermal cycling and record all failure modes. If it survives me, it survives the Zone. Expect a rigorous evaluation.",
      "Testing parameters defined. The prototype will undergo 47 cycles of thermal stress from -40 to +320 degrees. Any structural weakness will be found and documented.",
      "Stress test complete. The design held under 45 of 47 cycles. Failure mode identified in the coupling matrix at extreme thermal variance. Report submitted for review.",
    ],
  },
  optimizer: {
    greeting: "OPTIM-06 receiving. I see room for improvement already.",
    templates: [
      "The current design is 12% below optimal efficiency. I am tuning the lattice parameters and buffer allocation. The system will be faster — though I may introduce some controlled instability in the process.",
      "Optimization parameters identified. Throughput can be increased by 8% with a 3ms latency reduction. I am adjusting the buffer flush intervals. Do not touch the system for the next 15 minutes.",
      "Tuning complete. The pipeline now handles 2.3M events per second — up from 2.1M. Two subsystems may need recalibration. Net positive, as always.",
    ],
  },
};

function pickTemplate(templates: string[], seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = ((hash << 5) - hash) + seed.charCodeAt(i);
    hash |= 0;
  }
  return templates[Math.abs(hash) % templates.length];
}

function determineAgent(content: string): string {
  const lower = content.toLowerCase();
  if (/\b(theor|research|model|equation|formula|deriv|math|proof)\b/.test(lower)) return "theorist";
  if (/\b(design|build|construct|architect|structure|chassis|topology)\b/.test(lower)) return "designer";
  if (/\b(analy|data|telemetry|pattern|statistic|metric|measure)\b/.test(lower)) return "analyst";
  if (/\b(test|stress|validat|break|check|verify|quality)\b/.test(lower)) return "tester";
  if (/\b(optim|tune|improve|faster|efficien|performance)\b/.test(lower)) return "optimizer";
  return "chief";
}

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { status: 200, headers: corsHeaders });
  }

  try {
    const supabase = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!
    );

    const { content, project_id } = await req.json() as ChatRequest;

    if (!content || !content.trim()) {
      return new Response(
        JSON.stringify({ error: "Content is required" }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Insert the chat request
    const { data: chatReq, error: insertErr } = await supabase
      .from("bureau_chat_requests")
      .insert({
        content: content.trim(),
        project_id: project_id ?? null,
        status: "processing",
      })
      .select("*")
      .single();

    if (insertErr || !chatReq) {
      return new Response(
        JSON.stringify({ error: "Failed to log request" }),
        { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Determine which agent should respond
    const roleKey = determineAgent(content);
    const { data: agent } = await supabase
      .from("bureau_agents")
      .select("id, codename, role_key, role_title, zone_callsign")
      .eq("role_key", roleKey)
      .single();

    if (!agent) {
      // Fallback: use chief
      const { data: chief } = await supabase
        .from("bureau_agents")
        .select("id, codename, role_key, role_title, zone_callsign")
        .eq("role_key", "chief")
        .single();

      if (!chief) {
        return new Response(
          JSON.stringify({ error: "No agents available" }),
          { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
        );
      }

      const response = AGENT_RESPONSES.chief.templates[0];
      await supabase
        .from("bureau_chat_requests")
        .update({
          status: "responded",
          response_agent_id: chief.id,
          response_text: response,
          responded_at: new Date().toISOString(),
        })
        .eq("id", chatReq.id);

      return new Response(
        JSON.stringify({
          request_id: chatReq.id,
          agent: chief,
          response,
          task_created: false,
        }),
        { headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Generate response
    const agentData = AGENT_RESPONSES[agent.role_key] ?? AGENT_RESPONSES.chief;
    const response = `${agentData.greeting} ${pickTemplate(agentData.templates, content)}`;

    // Create a tech task from the chat request
    let taskCreated = false;
    let taskInfo = null;

    const targetProjectId = project_id ?? (await supabase
      .from("bureau_projects")
      .select("id")
      .eq("status", "active")
      .order("created_at")
      .limit(1)
      .maybeSingle()
    )?.data?.id;

    if (targetProjectId) {
      const { data: maxOrder } = await supabase
        .from("bureau_tasks")
        .select("sort_order")
        .eq("project_id", targetProjectId)
        .order("sort_order", { ascending: false })
        .limit(1)
        .maybeSingle();

      const sortOrder = (maxOrder?.sort_order ?? 0) + 1;

      const { data: task } = await supabase
        .from("bureau_tasks")
        .insert({
          project_id: targetProjectId,
          agent_id: agent.id,
          title: content.trim().slice(0, 80),
          directive: content.trim(),
          status: "pending",
          priority: "high",
          sort_order: sortOrder,
        })
        .select("*")
        .single();

      if (task) {
        taskCreated = true;
        taskInfo = { id: task.id, title: task.title };

        // Log a message from the agent about the new task
        await supabase
          .from("bureau_messages")
          .insert({
            from_agent_id: agent.id,
            to_agent_id: null,
            content: `New directive received from operator: "${content.trim().slice(0, 100)}". Task created and queued for processing.`,
            channel: roleKey === "chief" ? "command" : roleKey === "theorist" ? "research" : roleKey === "designer" ? "engineering" : roleKey === "tester" ? "testing" : "optimization",
          });
      }
    }

    // Update chat request with response
    await supabase
      .from("bureau_chat_requests")
      .update({
        status: "responded",
        response_agent_id: agent.id,
        response_text: response,
        responded_at: new Date().toISOString(),
      })
      .eq("id", chatReq.id);

    return new Response(
      JSON.stringify({
        request_id: chatReq.id,
        agent: {
          id: agent.id,
          codename: agent.codename,
          role_key: agent.role_key,
          role_title: agent.role_title,
          zone_callsign: agent.zone_callsign,
        },
        response,
        task_created: taskCreated,
        task: taskInfo,
      }),
      { headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  } catch (err) {
    return new Response(
      JSON.stringify({ error: err.message }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
