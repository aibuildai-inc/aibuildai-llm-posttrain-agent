"""Initialize a population, evaluate and mutate it across bounded generations, keep the best."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.initializer import InitializerAgent
from .agents.io import Individual, InitializerInput, MutationInput
from .agents.mutation import MutationAgent
from .io import EvolutionarySearchInput
from .programs.evaluate_individual import EvaluateIndividualInput, EvaluateIndividualOutput, EvaluateIndividualProgram


class EvolutionarySearch(Search[EvolutionarySearchInput, SearchOutput]):
    """A population persists across generations; offspring always derive from a selected parent."""

    async def explore(self) -> SearchOutput | Failure:
        initializer = InitializerAgent(
            input=InitializerInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                population_size=self.input.population_size,
                metric_name=self.input.metric_name,
            ),
        )
        initializer_handle = await self.ctx.spawn(
            initializer.run,
            "Initialize the starting population.",
            read=(task_data(),),
            capability=ExecutionCapability(
                wall_clock_seconds=self.input.initializer_wall_clock_seconds,
                gpus=0,
            ),
        )
        batch = await initializer_handle.result()

        population = batch.individuals
        producers: dict[str, Handle] = {
            individual.individual_id: initializer_handle for individual in population
        }
        incumbent: tuple[Individual, EvaluateIndividualOutput] | None = None

        for generation in range(self.input.max_generations):
            evaluated = await self._evaluate(
                population=population,
                source_dir=batch.source_dir,
                python_path=batch.python_path,
                producers=producers,
            )
            if len(evaluated) < self.input.min_feasible:
                return Failure(
                    kind=FailureKind.NO_OUTPUT,
                    reason=(
                        f"generation {generation}: {len(evaluated)} of {len(population)} "
                        f"individuals reached a metric; at least {self.input.min_feasible} required"
                    ),
                )

            for individual in population:
                outcome = evaluated.get(individual.individual_id)
                if outcome is not None and (incumbent is None or outcome.score > incumbent[1].score):
                    incumbent = (individual, outcome)

            if generation + 1 >= self.input.max_generations:
                break

            ranked = sorted(evaluated.items(), key=lambda item: item[1].score, reverse=True)
            elite_ids = [individual_id for individual_id, _ in ranked[: self.input.elite_count]]
            parent_ids = [individual_id for individual_id, _ in ranked[: self.input.parent_count]]
            by_id = {individual.individual_id: individual for individual in population}

            mutations = [
                MutationAgent(
                    input=MutationInput(
                        objective=self.input.objective,
                        parent=by_id[individual_id],
                        fitness_score=evaluated[individual_id].score,
                        generation=generation + 1,
                    ),
                )
                for individual_id in parent_ids
            ]
            handles = [
                await self.ctx.spawn(
                    mutation.run,
                    "Mutate this individual.",
                    upstream=(producers[individual_id],),
                    capture_failure=True,
                    read=(task_data(), producers[individual_id].files()),
                    capability=ExecutionCapability(
                        wall_clock_seconds=self.input.mutation_wall_clock_seconds,
                        gpus=0,
                    ),
                )
                for mutation, individual_id in zip(mutations, parent_ids, strict=True)
            ]
            await self.ctx.wait(handles)

            offspring: list[Individual] = []
            for handle in handles:
                outcome = await handle.result()
                if isinstance(outcome, Failure):
                    continue
                offspring.extend(outcome.offspring)
                for child in outcome.offspring:
                    producers[child.individual_id] = handle

            if not offspring:
                break

            elites = [by_id[individual_id] for individual_id in elite_ids]
            population = tuple((*elites, *offspring))[: self.input.population_size]

        if incumbent is None:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason="Evolutionary Search produced no valid incumbent.",
            )

        winner, fitness = incumbent
        return SearchOutput(
            output_dir=fitness.output_dir,
            score=fitness.score,
            components=((self.input.metric_name, fitness.score),),
        )

    async def _evaluate(
        self,
        *,
        population: tuple[Individual, ...],
        source_dir: str,
        python_path: str,
        producers: dict[str, Handle],
    ) -> dict[str, EvaluateIndividualOutput]:
        handles = {
            individual.individual_id: await self.ctx.spawn(
                EvaluateIndividualProgram(
                    input=EvaluateIndividualInput(
                        individual_id=individual.individual_id,
                        config_json=individual.config_json,
                        source_dir=source_dir,
                        python_path=python_path,
                        data_dir=self.input.data_dir,
                        metric_name=self.input.metric_name,
                    ),
                ).run,
                upstream=(producers[individual.individual_id],),
                capture_failure=True,
                read=(task_data(), producers[individual.individual_id].files()),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.evaluation_wall_clock_seconds,
                    gpus=1,
                ),
            )
            for individual in population
        }
        await self.ctx.wait(list(handles.values()))

        evaluated: dict[str, EvaluateIndividualOutput] = {}
        for individual_id, handle in handles.items():
            outcome = await handle.result()
            if not isinstance(outcome, Failure):
                evaluated[individual_id] = outcome
        return evaluated
