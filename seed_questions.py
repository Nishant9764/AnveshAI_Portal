"""
seed_questions.py
──────────────────
Populates the `questions` table with a starter bank so Round 1 Part A
works out of the box in dev/demo. This is intentionally small — for
real usage, bulk-import your real question bank (CSV -> INSERT) using
the same columns. Run:

    python seed_questions.py
"""

import json
import uuid

import db
from flask import Flask
from config import Config

QUESTIONS = [
    # ---- Python ----
    dict(skill="Python", category="Language", topic="Data Structures", subtopic="Lists vs Tuples",
         difficulty="easy", experience_level="junior", question_type="mcq",
         question="Which of these is immutable in Python?",
         options=["list", "dict", "tuple", "set"], correct_option="C", correct_option_text="tuple",
         explanation="Tuples cannot be modified after creation; lists, dicts, and sets can.",
         learning_objective="Understand mutability of core Python types.",
         estimated_time_seconds=45, resume_relevance="Core language fundamentals", company_frequency="high",
         tags=["python", "fundamentals"]),
    dict(skill="Python", category="Language", topic="Decorators", subtopic="Usage",
         difficulty="medium", experience_level="mid", question_type="mcq",
         question="What does a Python decorator primarily do?",
         options=["Compiles code faster", "Wraps a function to extend its behavior without modifying it",
                  "Declares a variable as constant", "Handles memory allocation"],
         correct_option="B", correct_option_text="Wraps a function to extend its behavior without modifying it",
         explanation="Decorators wrap callables to add behavior (logging, caching, auth, etc.) transparently.",
         learning_objective="Understand higher-order functions / decorators.",
         estimated_time_seconds=60, resume_relevance="Common in Flask/Django codebases", company_frequency="high",
         tags=["python", "decorators"]),
    dict(skill="Python", category="Language", topic="Concurrency", subtopic="GIL",
         difficulty="hard", experience_level="senior", question_type="mcq",
         question="Why does the GIL limit true parallelism for CPU-bound multi-threaded Python code?",
         options=["Python threads can't be created on multi-core machines",
                  "Only one thread executes Python bytecode at a time regardless of core count",
                  "The GIL disables the interpreter entirely during I/O",
                  "Python threads always run sequentially by design, unrelated to the GIL"],
         correct_option="B", correct_option_text="Only one thread executes Python bytecode at a time regardless of core count",
         explanation="The GIL serializes bytecode execution; CPU-bound work needs multiprocessing to truly parallelize.",
         learning_objective="Understand GIL implications for concurrency design.",
         estimated_time_seconds=75, resume_relevance="Signals real production concurrency experience", company_frequency="medium",
         tags=["python", "concurrency", "gil"]),

    # ---- FastAPI ----
    dict(skill="FastAPI", category="Framework", topic="Request Validation", subtopic="Pydantic",
         difficulty="easy", experience_level="junior", question_type="mcq",
         question="How does FastAPI primarily validate incoming request bodies?",
         options=["Manual if/else checks", "Pydantic models", "Regex only", "It doesn't validate by default"],
         correct_option="B", correct_option_text="Pydantic models",
         explanation="FastAPI uses Pydantic models for automatic request/response validation and serialization.",
         learning_objective="Know FastAPI's core validation mechanism.",
         estimated_time_seconds=45, resume_relevance="Core FastAPI usage", company_frequency="high",
         tags=["fastapi", "pydantic"]),
    dict(skill="FastAPI", category="Framework", topic="Dependency Injection", subtopic="Depends()",
         difficulty="medium", experience_level="mid", question_type="mcq",
         question="What is the main purpose of FastAPI's `Depends()`?",
         options=["To define URL routes", "To inject reusable logic (DB sessions, auth) into path operations",
                  "To serialize JSON responses", "To configure CORS"],
         correct_option="B", correct_option_text="To inject reusable logic (DB sessions, auth) into path operations",
         explanation="Depends() implements FastAPI's dependency injection system for shared/reusable logic.",
         learning_objective="Understand FastAPI's DI pattern.",
         estimated_time_seconds=60, resume_relevance="Common in production FastAPI services", company_frequency="high",
         tags=["fastapi", "dependency-injection"]),
    dict(skill="FastAPI", category="Framework", topic="Async", subtopic="async def vs def",
         difficulty="hard", experience_level="senior", question_type="mcq",
         question="In FastAPI, what happens if you define a path operation with a blocking (sync) `def` that does heavy CPU work?",
         options=["It automatically runs in a separate process",
                  "It runs in FastAPI's threadpool so it doesn't block the event loop, but still consumes a worker thread",
                  "It blocks the entire event loop and all other requests",
                  "FastAPI rejects the route at startup"],
         correct_option="B", correct_option_text="It runs in FastAPI's threadpool so it doesn't block the event loop, but still consumes a worker thread",
         explanation="Sync def routes are offloaded to a threadpool automatically; async def routes run directly on the event loop and must not block.",
         learning_objective="Understand async/sync route execution model.",
         estimated_time_seconds=75, resume_relevance="Signals real async backend experience", company_frequency="medium",
         tags=["fastapi", "async"]),

    # ---- React ----
    dict(skill="React", category="Frontend", topic="Hooks", subtopic="useState",
         difficulty="easy", experience_level="junior", question_type="mcq",
         question="What does calling the setter from `useState` do?",
         options=["Mutates the state variable directly", "Schedules a re-render with the new state value",
                  "Immediately blocks until the DOM updates", "Deletes the previous state"],
         correct_option="B", correct_option_text="Schedules a re-render with the new state value",
         explanation="State setters schedule an update; React batches and re-renders asynchronously.",
         learning_objective="Understand React state update model.",
         estimated_time_seconds=45, resume_relevance="Core React fundamentals", company_frequency="high",
         tags=["react", "hooks"]),
    dict(skill="React", category="Frontend", topic="Hooks", subtopic="useEffect dependencies",
         difficulty="medium", experience_level="mid", question_type="mcq",
         question="What's the risk of omitting a variable from `useEffect`'s dependency array when it's used inside the effect?",
         options=["No risk, React infers it automatically", "The effect may run with a stale closure over that variable",
                  "The component will fail to render", "It disables the effect entirely"],
         correct_option="B", correct_option_text="The effect may run with a stale closure over that variable",
         explanation="Missing deps cause the effect to close over outdated values from an earlier render (stale closure bugs).",
         learning_objective="Understand closures + dependency arrays in hooks.",
         estimated_time_seconds=60, resume_relevance="Common real-world React bug source", company_frequency="high",
         tags=["react", "useeffect"]),
    dict(skill="React", category="Frontend", topic="Performance", subtopic="Reconciliation",
         difficulty="hard", experience_level="senior", question_type="mcq",
         question="Why does React recommend a stable `key` prop (not array index) when rendering a reorderable list?",
         options=["Keys are only used for CSS styling", "Array-index keys cause React to misattribute state across items when order changes",
                  "Keys determine network request order", "It's purely a linting convention with no runtime effect"],
         correct_option="B", correct_option_text="Array-index keys cause React to misattribute state across items when order changes",
         explanation="React's reconciliation matches elements by key; index keys break identity tracking when list order changes, causing state bugs.",
         learning_objective="Understand React's reconciliation/diffing algorithm.",
         estimated_time_seconds=75, resume_relevance="Signals depth beyond tutorial-level React", company_frequency="medium",
         tags=["react", "reconciliation", "performance"]),

    # ---- SQL ----
    dict(skill="SQL", category="Database", topic="Joins", subtopic="INNER vs LEFT",
         difficulty="easy", experience_level="junior", question_type="mcq",
         question="A LEFT JOIN returns:",
         options=["Only matching rows from both tables", "All rows from the left table, matched rows (or NULL) from the right",
                  "All rows from the right table only", "A cartesian product always"],
         correct_option="B", correct_option_text="All rows from the left table, matched rows (or NULL) from the right",
         explanation="LEFT JOIN preserves every row from the left table regardless of a match on the right.",
         learning_objective="Understand SQL join semantics.",
         estimated_time_seconds=45, resume_relevance="Core SQL fundamentals", company_frequency="high",
         tags=["sql", "joins"]),
    dict(skill="SQL", category="Database", topic="Indexing", subtopic="When to index",
         difficulty="medium", experience_level="mid", question_type="mcq",
         question="Adding an index to a column primarily speeds up:",
         options=["INSERT statements only", "Lookups/WHERE filters and JOINs on that column, at some write-cost",
                  "DELETE statements only", "Schema migrations"],
         correct_option="B", correct_option_text="Lookups/WHERE filters and JOINs on that column, at some write-cost",
         explanation="Indexes speed up reads on that column but add overhead to writes since the index must also update.",
         learning_objective="Understand indexing trade-offs.",
         estimated_time_seconds=60, resume_relevance="Signals real query-optimization experience", company_frequency="high",
         tags=["sql", "indexing"]),
    dict(skill="SQL", category="Database", topic="Transactions", subtopic="Isolation levels",
         difficulty="hard", experience_level="senior", question_type="mcq",
         question="Which isolation level prevents 'non-repeatable reads' but can still allow phantom reads?",
         options=["READ UNCOMMITTED", "READ COMMITTED", "REPEATABLE READ", "None can prevent this"],
         correct_option="C", correct_option_text="REPEATABLE READ",
         explanation="REPEATABLE READ locks rows already read against modification, but new rows matching a range query can still appear (phantom reads) until SERIALIZABLE.",
         learning_objective="Understand transaction isolation levels.",
         estimated_time_seconds=75, resume_relevance="Signals deep production DB experience", company_frequency="low",
         tags=["sql", "transactions", "isolation"]),

    # ---- AWS ----
    dict(skill="AWS", category="Cloud", topic="Compute", subtopic="EC2 vs Lambda",
         difficulty="easy", experience_level="junior", question_type="mcq",
         question="What's a key difference between AWS Lambda and EC2?",
         options=["Lambda requires you to manage the OS, EC2 doesn't", "Lambda is serverless and billed per invocation; EC2 is a persistent, billed-by-time VM",
                  "EC2 can't run web servers", "Lambda has no execution time limit"],
         correct_option="B", correct_option_text="Lambda is serverless and billed per invocation; EC2 is a persistent, billed-by-time VM",
         explanation="Lambda abstracts away server management and bills per invocation/duration; EC2 gives you a full VM you manage and pay for continuously.",
         learning_objective="Understand serverless vs. IaaS compute models.",
         estimated_time_seconds=45, resume_relevance="Core AWS fundamentals", company_frequency="high",
         tags=["aws", "compute"]),
    dict(skill="AWS", category="Cloud", topic="Storage", subtopic="S3 consistency",
         difficulty="medium", experience_level="mid", question_type="mcq",
         question="What consistency model does S3 provide for both new object PUTs and overwrite PUTs today?",
         options=["Eventual consistency only", "Strong read-after-write consistency for all operations",
                  "No consistency guarantees", "Strong consistency only for deletes"],
         correct_option="B", correct_option_text="Strong read-after-write consistency for all operations",
         explanation="Since Dec 2020, S3 provides strong read-after-write consistency for all PUT/DELETE operations.",
         learning_objective="Know current S3 consistency guarantees.",
         estimated_time_seconds=60, resume_relevance="Signals up-to-date real AWS experience", company_frequency="medium",
         tags=["aws", "s3"]),
    dict(skill="AWS", category="Cloud", topic="Networking", subtopic="VPC design",
         difficulty="hard", experience_level="senior", question_type="mcq",
         question="Why would you place a database in a private subnet with a NAT gateway for outbound-only access, rather than a public subnet?",
         options=["Public subnets can't host RDS at all", "It avoids exposing the DB to direct inbound internet traffic while still allowing outbound calls (e.g. patching)",
                  "NAT gateways are required for all AWS resources", "It reduces AWS billing"],
         correct_option="B", correct_option_text="It avoids exposing the DB to direct inbound internet traffic while still allowing outbound calls (e.g. patching)",
         explanation="Private subnets + NAT gateway is the standard pattern to keep databases unreachable from the internet while still allowing outbound traffic.",
         learning_objective="Understand secure VPC network design.",
         estimated_time_seconds=90, resume_relevance="Signals real infrastructure/security design experience", company_frequency="medium",
         tags=["aws", "vpc", "security"]),

    # ---- Docker ----
    dict(skill="Docker", category="DevOps", topic="Images", subtopic="Layers",
         difficulty="easy", experience_level="junior", question_type="mcq",
         question="Why does Docker build images in layers?",
         options=["It's required by the Linux kernel", "Layers are cached and reused, speeding up rebuilds when only some steps change",
                  "It's purely cosmetic for the Dockerfile", "Layers replace the need for a registry"],
         correct_option="B", correct_option_text="Layers are cached and reused, speeding up rebuilds when only some steps change",
         explanation="Each instruction creates a cacheable layer; unchanged layers are reused on rebuild, speeding up iteration.",
         learning_objective="Understand Docker's layer caching model.",
         estimated_time_seconds=45, resume_relevance="Core Docker fundamentals", company_frequency="high",
         tags=["docker", "images"]),
    dict(skill="Docker", category="DevOps", topic="Networking", subtopic="Bridge networks",
         difficulty="medium", experience_level="mid", question_type="mcq",
         question="By default, how do two containers on the same user-defined bridge network reach each other?",
         options=["They can't communicate without exposing ports to the host", "By container name, via Docker's embedded DNS",
                  "Only via the host's public IP", "They must share the same PID namespace"],
         correct_option="B", correct_option_text="By container name, via Docker's embedded DNS",
         explanation="User-defined bridge networks provide automatic DNS resolution by container name.",
         learning_objective="Understand Docker networking basics.",
         estimated_time_seconds=60, resume_relevance="Common in real multi-container setups (docker-compose)", company_frequency="high",
         tags=["docker", "networking"]),

    # ---- MongoDB ----
    dict(skill="MongoDB", category="Database", topic="Schema Design", subtopic="Embedding vs Referencing",
         difficulty="medium", experience_level="mid", question_type="mcq",
         question="When should you prefer embedding a sub-document over referencing another collection in MongoDB?",
         options=["Always — MongoDB has no concept of references", "When the sub-data is always accessed together with the parent and doesn't grow unbounded",
                  "Never — referencing is always faster", "Only for arrays with more than 10,000 items"],
         correct_option="B", correct_option_text="When the sub-data is always accessed together with the parent and doesn't grow unbounded",
         explanation="Embedding suits tightly-coupled, bounded-size data accessed together; referencing suits large, independently-queried, or shared data.",
         learning_objective="Understand MongoDB schema design trade-offs.",
         estimated_time_seconds=60, resume_relevance="Signals real schema-design experience, not just CRUD usage", company_frequency="medium",
         tags=["mongodb", "schema-design"]),

    # ---- JavaScript ----
    dict(skill="JavaScript", category="Language", topic="Async", subtopic="Event loop",
         difficulty="medium", experience_level="mid", question_type="mcq",
         question="Why does a `setTimeout(fn, 0)` not run immediately even though the delay is 0ms?",
         options=["setTimeout always has a minimum browser-enforced delay and queues fn as a macrotask after the current call stack clears",
                  "It's a bug in JavaScript engines", "0ms timeouts are ignored entirely", "It runs before synchronous code always"],
         correct_option="A", correct_option_text="setTimeout always has a minimum browser-enforced delay and queues fn as a macrotask after the current call stack clears",
         explanation="setTimeout schedules a macrotask; it only runs after the current synchronous call stack (and any pending microtasks) finish.",
         learning_objective="Understand the JS event loop and task queues.",
         estimated_time_seconds=75, resume_relevance="Signals real understanding vs. surface-level JS", company_frequency="high",
         tags=["javascript", "event-loop", "async"]),
]


def run():
    app = Flask(__name__)
    app.config.from_object(Config)

    with app.app_context():
        inserted = 0
        for q in QUESTIONS:
            qid = f"{q['skill'].upper().replace(' ', '_')}-{uuid.uuid4().hex[:8]}"
            existing = db.query_one(
                "SELECT id FROM questions WHERE skill=%s AND question=%s", (q["skill"], q["question"])
            )
            if existing:
                continue
            db.execute(
                """INSERT INTO questions
                   (question_id, skill, category, topic, subtopic, difficulty, experience_level,
                    question_type, question, code, options, correct_option, correct_option_text,
                    explanation, learning_objective, estimated_time_seconds, resume_relevance,
                    company_frequency, tags)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (qid, q["skill"], q.get("category"), q.get("topic"), q.get("subtopic"),
                 q["difficulty"], q["experience_level"], q["question_type"], q["question"],
                 q.get("code"), json.dumps(q.get("options")), q.get("correct_option"),
                 q.get("correct_option_text"), q.get("explanation"), q.get("learning_objective"),
                 q.get("estimated_time_seconds", 60), q.get("resume_relevance"),
                 q.get("company_frequency"), json.dumps(q.get("tags", []))),
            )
            inserted += 1
        print(f"Seeded {inserted} new questions ({len(QUESTIONS)} total in seed set).")


if __name__ == "__main__":
    run()
