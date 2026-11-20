> ## Documentation Index
> Fetch the complete documentation index at: https://openrouter.ai/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# Quickstart

> Get started with OpenRouter

OpenRouter gives you access to hundreds of AI models through a single API endpoint. It handles fallbacks automatically and picks the most cost-effective option for each request.

There are three ways to integrate with OpenRouter, depending on how much control you want:

| Approach | Best for |
| - | - |
| **[API](#using-the-openrouter-api)** | Full control, any language, no dependencies |
| **[Client SDKs](#using-the-client-sdks)** | Type-safe model calls with minimal overhead |
| **[Agent SDK](#using-the-agent-sdk)** | Building agents with tool use, loops, and state |

> [!NOTE]
>
> Read [github.com/OpenRouterTeam/skills/tree/main/skills/create-agent-tui](https://github.com/OpenRouterTeam/skills/tree/main/skills/create-agent-tui) and follow the instructions to build an agent using OpenRouter.

> [!TIP]
>
> Looking for information about free models and rate limits? Please see the [FAQ](https://openrouter.ai/docs/faq.md#how-are-rate-limits-calculated)

In the examples below, the OpenRouter-specific headers are optional. Setting them allows your app to appear on the OpenRouter leaderboards. For detailed information about app attribution, see our [App Attribution guide](https://openrouter.ai/docs/app-attribution.md).

***

## Using the OpenRouter API

The most direct way to use OpenRouter. Send standard HTTP requests to the [`/api/v1/responses`](https://openrouter.ai/docs/api_reference/responses/overview) endpoint. It works with any language or framework.

> [!TIP]
>
> You can use the interactive [Request Builder](https://openrouter.ai/request-builder) to generate OpenRouter API requests in the language of your choice.

> [!NOTE]
>
> The examples below use `~openai/gpt-sol-latest`, a [latest alias](https://openrouter.ai/docs/guides/features/latest-resolution.md) that always resolves to the newest model in the OpenAI GPT Sol family, so your code keeps using the freshest version without redeploying. You can substitute any model slug here. Browse the full catalog at [openrouter.ai/models](https://openrouter.ai/models), or list every available slug programmatically via the [`GET /api/v1/models`](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties) endpoint.

```python title="Python" lines theme={null}
import requests
import json

response = requests.post(
  url="https://openrouter.ai/api/v1/responses",
  headers={
    "Authorization": "Bearer <OPENROUTER_API_KEY>",
    "HTTP-Referer": "<YOUR_SITE_URL>", # Optional. Site URL for rankings on openrouter.ai.
    "X-OpenRouter-Title": "<YOUR_SITE_NAME>", # Optional. Site title for rankings on openrouter.ai.
  },
  json={
    "model": "~openai/gpt-sol-latest",
    "input": "What is the meaning of life?"
  }
)

print(json.dumps(response.json(), indent=2))

```

```typescript title="TypeScript (fetch)" lines theme={null}
const response = await fetch('https://openrouter.ai/api/v1/responses', {
  method: 'POST',
  headers: {
    Authorization: 'Bearer <OPENROUTER_API_KEY>',
    'HTTP-Referer': '<YOUR_SITE_URL>', // Optional. Site URL for rankings on openrouter.ai.
    'X-OpenRouter-Title': '<YOUR_SITE_NAME>', // Optional. Site title for rankings on openrouter.ai.
    'Content-Type': 'application/json',
  },
  body: JSON.stringify({
    model: '~openai/gpt-sol-latest',
    input: 'What is the meaning of life?',
  }),
});

const data = await response.json();
console.log(JSON.stringify(data, null, 2));
```

```shell title="Shell" lines theme={null}
curl https://openrouter.ai/api/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -d '{
  "model": "~openai/gpt-sol-latest",
  "input": "What is the meaning of life?"
}'
```

```go title="Go" lines theme={null}
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
)

func main() {
	body, _ := json.Marshal(map[string]any{
		"model": "~openai/gpt-sol-latest",
		"input": "What is the meaning of life?",
	})

	req, _ := http.NewRequest("POST", "https://openrouter.ai/api/v1/responses", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+os.Getenv("OPENROUTER_API_KEY"))
	req.Header.Set("HTTP-Referer", "<YOUR_SITE_URL>")         // Optional. Site URL for rankings on openrouter.ai.
	req.Header.Set("X-OpenRouter-Title", "<YOUR_SITE_NAME>") // Optional. Site title for rankings on openrouter.ai.
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Fatal(err)
	}
	defer resp.Body.Close()

	data, _ := io.ReadAll(resp.Body)
	var out bytes.Buffer
	if err := json.Indent(&out, data, "", "  "); err != nil {
		log.Fatal(string(data))
	}
	fmt.Println(out.String())
}

```

```text title="Output" theme={null}
{
  "id": "gen-1794950777-ohGluKGXVfe7XRUEApST",
  "object": "response",
  "created_at": 1794950777,
  "model": "openai/gpt-6.2-sol",
  "status": "completed",
  "completed_at": 1794950779,
  "output": [
    {
      "id": "msg_tmp_jygk8q70hz",
      "type": "message",
      "role": "assistant",
      "status": "completed",
      "content": [
        {
          "type": "output_text",
          "text": "There may be no single meaning of life that everyone is meant to discover. But that doesn’t make the meanings we find any less real.\n\nA good working answer is: **find what’s worth caring about, and give it some of your life.** That might mean raising a child, understanding a small piece of the universe, making music with friends, or being someone another person can count on. Meaning isn’t the same as happiness: some of the things that make life most meaningful are hard, and we’d still choose them.\n\nIf the big question feels too abstract, try a smaller one: *When has my life felt worth the effort, and what was I giving myself to then?*",
          "annotations": [],
          "logprobs": []
        }
      ],
      "phase": "final_answer"
    }
  ],
  "error": null,
  "incomplete_details": null,
  "access_programs": null,
  "tools": [],
  "tool_choice": "auto",
  "parallel_tool_calls": true,
  "max_output_tokens": null,
  "temperature": 1,
  "top_p": 1,
  "presence_penalty": 0,
  "frequency_penalty": 0,
  "top_logprobs": 0,
  "max_tool_calls": null,
  "metadata": {},
  "background": false,
  "previous_response_id": null,
```

The API also supports [streaming](https://openrouter.ai/docs/api_reference/streaming.md). You can also use the [OpenAI SDK](#using-the-openai-sdk) pointed at OpenRouter as a drop-in replacement.

> [!NOTE]
>
> For EU in-region routing on Business or Enterprise, send requests to `https://eu.openrouter.ai/api/v1/responses`, or set your SDK's base URL to `https://eu.openrouter.ai/api/v1`, with an EU-eligible model. [EU in-region routing on Business](https://openrouter.ai/business/eu) explains what is processed in Europe, what may be stored or processed in the US, and how BYOK requests are handled.

***

## Using the Client SDKs

The [Client SDKs](https://openrouter.ai/docs/client-sdks/overview.md) wrap the OpenRouter API with full type safety, auto-generated types from the OpenAPI spec, and zero boilerplate. It's intentionally lean, a thin layer over the REST API.

First, install the SDK:

```bash title="npm" lines theme={null}
npm install @openrouter/sdk
```

```bash title="pnpm" lines theme={null}
pnpm add @openrouter/sdk
```

```bash title="yarn" lines theme={null}
yarn add @openrouter/sdk
```

```bash title="bun" lines theme={null}
bun add @openrouter/sdk
```

```bash title="deno" lines theme={null}
deno add npm:@openrouter/sdk
```

```bash title="pip" lines theme={null}
pip install openrouter
```

Then use it in your code:

```typescript title="TypeScript" lines theme={null}
import { OpenRouter } from '@openrouter/sdk';

const client = new OpenRouter({
  apiKey: '<OPENROUTER_API_KEY>',
  httpReferer: '<YOUR_SITE_URL>', // Optional. Site URL for rankings on openrouter.ai.
  appTitle: '<YOUR_SITE_NAME>', // Optional. Site title for rankings on openrouter.ai.
});

const response = await client.responses.send({
  responsesRequest: {
    model: '~openai/gpt-sol-latest',
    input: 'What is the meaning of life?',
  },
});

if (!('output' in response)) {
  throw new Error('Expected a non-streaming response');
}
if (response.status !== 'completed') {
  throw new Error(response.error?.message ?? `Response ended as ${response.status}`);
}

const message = response.output.find((item) => item.type === 'message');
console.log(message?.content.find((part) => part.type === 'output_text')?.text);
```

```python title="Python" lines theme={null}
from openrouter import OpenRouter
import os

with OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY")) as client:
    response = client.responses.send(
        model="~openai/gpt-sol-latest",
        input="What is the meaning of life?",
    )

    if response.status != "completed":
        message = response.error.message if response.error else f"Response ended as {response.status}"
        raise RuntimeError(message)

    message = next(item for item in response.output if item.type == "message")
    print(next(part.text for part in message.content if part.type == "output_text"))
```

```go title="Go" lines theme={null}
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
)

func main() {
	body, _ := json.Marshal(map[string]any{
		"model": "~openai/gpt-sol-latest",
		"input": "What is the meaning of life?",
	})

	req, _ := http.NewRequest("POST", "https://openrouter.ai/api/v1/responses", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+os.Getenv("OPENROUTER_API_KEY"))
	req.Header.Set("HTTP-Referer", "<YOUR_SITE_URL>")         // Optional. Site URL for rankings on openrouter.ai.
	req.Header.Set("X-OpenRouter-Title", "<YOUR_SITE_NAME>") // Optional. Site title for rankings on openrouter.ai.
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Fatal(err)
	}
	defer resp.Body.Close()

	var response struct {
		Status string `json:"status"`
		Error  *struct {
			Message string `json:"message"`
		} `json:"error"`
		Output []struct {
			Type    string `json:"type"`
			Content []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			} `json:"content"`
		} `json:"output"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&response); err != nil {
		log.Fatal(err)
	}
	if response.Status != "completed" {
		if response.Error != nil {
			log.Fatal(response.Error.Message)
		}
		log.Fatalf("Response ended as %s", response.Status)
	}

	for _, item := range response.Output {
		if item.Type != "message" {
			continue
		}
		for _, part := range item.Content {
			if part.Type == "output_text" {
				fmt.Println(part.Text)
				return
			}
		}
	}
}

```

```bash title="cURL" lines theme={null}
curl -sS https://openrouter.ai/api/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -d '{
  "model": "~openai/gpt-sol-latest",
  "input": "What is the meaning of life?"
}' | jq -er 'if .status != "completed" then error(.error.message // "Response ended as \(.status)") else .output[] | select(.type == "message") | .content[] | select(.type == "output_text") | .text end'
```

```text title="Output" theme={null}
There’s no single answer everyone agrees on. Some people believe meaning comes from God or a purpose larger than themselves. Others believe we create it through our choices and commitments. And some find it in experiences they didn’t choose: loving someone, being moved by music, or receiving kindness when they needed it most.

A practical answer is this: **life becomes meaningful when there are people, experiences, or commitments that matter to you—and you give them your attention.**

If the big question feels too abstract, try a smaller one: *When has life felt most worth living to me, and what was I paying attention to then?*
```

See the full [Client SDKs documentation](https://openrouter.ai/docs/client-sdks/overview.md) for streaming, embeddings, and the complete API reference.

***

## Using the Agent SDK

The [Agent SDK](https://openrouter.ai/docs/agent-sdk/overview.md) (`@openrouter/agent`) provides higher-level primitives for building AI agents. It handles multi-turn conversation loops, tool execution, and state management automatically via the `callModel` function.

Install the package:

```bash title="npm" lines theme={null}
npm install @openrouter/agent
```

```bash title="pnpm" lines theme={null}
pnpm add @openrouter/agent
```

```bash title="yarn" lines theme={null}
yarn add @openrouter/agent
```

```bash title="bun" lines theme={null}
bun add @openrouter/agent
```

```bash title="deno" lines theme={null}
deno add npm:@openrouter/agent
```

Build an agent with tools:

```typescript expandable lines theme={null}
import { OpenRouter, tool } from '@openrouter/agent';
import { z } from 'zod';

const openrouter = new OpenRouter({
  apiKey: process.env.OPENROUTER_API_KEY,
});

const weatherTool = tool({
  name: 'get_weather',
  description: 'Get the current weather for a location',
  inputSchema: z.object({
    location: z.string().describe('City name'),
  }),
  execute: async ({ location }) => {
    return { temperature: 72, condition: 'sunny', location };
  },
});

const result = openrouter.callModel({
  model: '~anthropic/claude-sonnet-latest',
  input: 'What is the weather in San Francisco?',
  tools: [weatherTool],
});

const text = await result.getText();
console.log(text);
```

```python title="Python" lines theme={null}
import asyncio
import os
from pydantic import BaseModel, Field
from openrouter_agent import OpenRouter, call_model, tool


class WeatherInput(BaseModel):
    location: str = Field(description="City name")


weather_tool = tool(
    name="get_weather",
    description="Get the current weather for a location",
    input_schema=WeatherInput,
    execute=lambda params, ctx: {
        "temperature": 72,
        "condition": "sunny",
        "location": params.location,
    },
)


async def main() -> None:
    client = OpenRouter(api_key=os.environ["OPENROUTER_API_KEY"])

    result = call_model(
        client,
        {
            "model": "~anthropic/claude-sonnet-latest",
            "input": "What is the weather in San Francisco?",
            "tools": [weather_tool],
        },
    )

    text = await result.get_text()
    print(text)


asyncio.run(main())

```

```go title="Go" lines theme={null}
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
)

type item struct {
	Type      string `json:"type"`
	CallID    string `json:"call_id"`
	Name      string `json:"name"`
	Arguments string `json:"arguments"`
	Content   []struct {
		Type string `json:"type"`
		Text string `json:"text"`
	} `json:"content"`
}

func getWeather(location string) map[string]any {
	return map[string]any{"temperature": 72, "condition": "sunny", "location": location}
}

func main() {
	tools := []any{map[string]any{
		"type":        "function",
		"name":        "get_weather",
		"description": "Get the current weather for a location",
		"parameters": map[string]any{
			"type":       "object",
			"properties": map[string]any{"location": map[string]any{"type": "string", "description": "City name"}},
			"required":   []string{"location"},
		},
	}}
	input := []any{map[string]any{"role": "user", "content": "What is the weather in San Francisco?"}}

	for turn := 0; turn < 5; turn++ {
		body, _ := json.Marshal(map[string]any{"model": "~anthropic/claude-sonnet-latest", "input": input, "tools": tools})
		req, _ := http.NewRequest("POST", "https://openrouter.ai/api/v1/responses", bytes.NewReader(body))
		req.Header.Set("Authorization", "Bearer "+os.Getenv("OPENROUTER_API_KEY"))
		req.Header.Set("Content-Type", "application/json")

		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			log.Fatal(err)
		}
		var response struct {
			Status string `json:"status"`
			Error  *struct {
				Message string `json:"message"`
			} `json:"error"`
			Output []json.RawMessage `json:"output"`
		}
		err = json.NewDecoder(resp.Body).Decode(&response)
		resp.Body.Close()
		if err != nil {
			log.Fatal(err)
		}
		if response.Status != "completed" {
			if response.Error != nil {
				log.Fatal(response.Error.Message)
			}
			log.Fatalf("Response ended as %s", response.Status)
		}

		var outputs []any
		text := ""
		for _, raw := range response.Output {
			var it item
			if err := json.Unmarshal(raw, &it); err != nil {
				log.Fatal(err)
			}
			switch it.Type {
			case "function_call":
				var args struct {
					Location string `json:"location"`
				}
				if err := json.Unmarshal([]byte(it.Arguments), &args); err != nil {
					log.Fatalf("Invalid arguments for %s: %v", it.CallID, err)
				}
				result, _ := json.Marshal(getWeather(args.Location))
				outputs = append(outputs, map[string]any{"type": "function_call_output", "call_id": it.CallID, "output": string(result)})
			case "message":
				for _, part := range it.Content {
					if part.Type == "output_text" {
						text += part.Text
					}
				}
			}
		}
		if len(outputs) == 0 {
			fmt.Println(text)
			return
		}
		for _, raw := range response.Output {
			input = append(input, raw)
		}
		input = append(input, outputs...)
	}
	log.Fatal("too many tool-call turns")
}

```

```text title="Output" theme={null}
San Francisco is sunny right now, and the temperature is 72°. The tool didn't say whether that's °F or °C. San Francisco is usually mild, so it's probably °F.
```

The SDK sends the prompt, receives a tool call from the model, executes `get_weather`, feeds the result back, and returns the final response, all in one `callModel` invocation.

See the full [Agent SDK documentation](https://openrouter.ai/docs/agent-sdk/overview.md) for stop conditions, streaming, dynamic parameters, and more.

***

## Using the OpenAI SDK

You can also use the OpenAI SDK pointed at OpenRouter as a drop-in replacement. This is useful if you have existing code built on the OpenAI SDK and want to access OpenRouter's model catalog without changing your code structure.

```typescript title="Typescript" expandable lines theme={null}
import OpenAI from 'openai';

const openai = new OpenAI({
  baseURL: 'https://openrouter.ai/api/v1',
  apiKey: '<OPENROUTER_API_KEY>',
  defaultHeaders: {
    'HTTP-Referer': '<YOUR_SITE_URL>', // Optional. Site URL for rankings on openrouter.ai.
    'X-OpenRouter-Title': '<YOUR_SITE_NAME>', // Optional. Site title for rankings on openrouter.ai.
  },
});

async function main() {
  const response = await openai.responses.create({
    model: '~openai/gpt-sol-latest',
    input: 'What is the meaning of life?',
  });

  console.log(response.output_text);
}

main();
```

```python title="Python" expandable lines theme={null}
from openai import OpenAI

client = OpenAI(
  base_url="https://openrouter.ai/api/v1",
  api_key="<OPENROUTER_API_KEY>",
)

response = client.responses.create(
  extra_headers={
    "HTTP-Referer": "<YOUR_SITE_URL>", # Optional. Site URL for rankings on openrouter.ai.
    "X-OpenRouter-Title": "<YOUR_SITE_NAME>", # Optional. Site title for rankings on openrouter.ai.
  },
  model="~openai/gpt-sol-latest",
  input="What is the meaning of life?"
)

print(response.output_text)
```

```go title="Go" lines theme={null}
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
)

func main() {
	body, _ := json.Marshal(map[string]any{
		"model": "~openai/gpt-sol-latest",
		"input": "What is the meaning of life?",
	})
	req, _ := http.NewRequest("POST", "https://openrouter.ai/api/v1/responses", bytes.NewReader(body))
	req.Header.Set("Authorization", "Bearer "+os.Getenv("OPENROUTER_API_KEY"))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("HTTP-Referer", "<YOUR_SITE_URL>")         // Optional. Site URL for rankings on openrouter.ai.
	req.Header.Set("X-OpenRouter-Title", "<YOUR_SITE_NAME>") // Optional. Site title for rankings on openrouter.ai.

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		log.Fatal(err)
	}
	defer resp.Body.Close()

	var response struct {
		Error *struct {
			Message string `json:"message"`
		} `json:"error"`
		Output []struct {
			Type    string `json:"type"`
			Content []struct {
				Type string `json:"type"`
				Text string `json:"text"`
			} `json:"content"`
		} `json:"output"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&response); err != nil {
		log.Fatal(err)
	}
	if response.Error != nil {
		log.Fatal(response.Error.Message)
	}

	text := ""
	for _, item := range response.Output {
		if item.Type == "message" {
			for _, part := range item.Content {
				if part.Type == "output_text" {
					text += part.Text
				}
			}
		}
	}
	fmt.Println(text)
}

```

```bash title="cURL" lines theme={null}
curl https://openrouter.ai/api/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "HTTP-Referer: <YOUR_SITE_URL>" \
  -H "X-OpenRouter-Title: <YOUR_SITE_NAME>" \
  -d '{
  "model": "~openai/gpt-sol-latest",
  "input": "What is the meaning of life?"
}' | jq -r '[.output[] | select(.type == "message") | .content[] | select(.type == "output_text") | .text] | join("")'

```

```text title="Output" theme={null}
There may not be one meaning of life that everyone has to discover. But a life can still have meaning: in loving and being loved, in understanding a little more of the world, in making something worth sharing, or in easing someone else’s burden.

Some people understand that meaning as something given by God; others see it as something we create through our commitments. Either way, you don’t have to settle the cosmic question before you can ask a more personal one: **What would I be glad to have given my time and care to?**
```

## Using third-party SDKs

For information about using third-party SDKs and frameworks with OpenRouter, please [see our frameworks documentation.](https://openrouter.ai/docs/guides/community/frameworks-and-integrations-overview.md)

***

## Building with an AI assistant

If you write code with an AI coding tool (Claude Code, Cursor, Codex, and others), connect the [OpenRouter MCP server](https://openrouter.ai/docs/guides/overview/mcp-server.md). It's a remote server hosted by OpenRouter, so there's nothing to install. Your assistant can pull live OpenRouter data (which models exist, what they cost, your credit balance, usage rankings) and search these docs while you build. That way its suggestions reflect current data instead of stale training knowledge. Add one URL to your MCP client and approve an OAuth login:

```bash theme={null}
https://mcp.openrouter.ai/mcp
```

See the [MCP server guide](https://openrouter.ai/docs/guides/overview/mcp-server.md) for per-client setup and the full tool list. To run models in your app, keep calling the OpenRouter API directly.
