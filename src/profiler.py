"""
On-demand profiling for OpenClaw.

Supports:
- CPU profiling (cProfile)
- Memory profiling (memory_profiler)
- Flame graph generation
"""

import asyncio
import cProfile
import io
import logging
import pstats
import tempfile
from pathlib import Path
from typing import Optional

try:
    from memory_profiler import profile as memory_profile
    MEMORY_PROFILER_AVAILABLE = True
except ImportError:
    MEMORY_PROFILER_AVAILABLE = False
    memory_profile = lambda func: func  # noqa: E731

logger = logging.getLogger(__name__)


class Profiler:
    """On-demand profiler for performance analysis."""
    
    def __init__(self):
        self._cpu_profiler: Optional[cProfile.Profile] = None
        self._is_profiling = False
        self._profile_start_time: Optional[float] = None
    
    def start_cpu_profiling(self):
        """Start CPU profiling."""
        if self._is_profiling:
            raise RuntimeError("Profiling already active")
        
        self._cpu_profiler = cProfile.Profile()
        self._cpu_profiler.enable()
        self._is_profiling = True
        
        import time
        self._profile_start_time = time.time()
        
        logger.info("CPU profiling started")
    
    def stop_cpu_profiling(self) -> str:
        """Stop CPU profiling and return formatted stats."""
        if not self._is_profiling or not self._cpu_profiler:
            raise RuntimeError("No active profiling session")
        
        self._cpu_profiler.disable()
        self._is_profiling = False
        
        import time
        duration = time.time() - self._profile_start_time if self._profile_start_time else 0
        
        # Create stats
        s = io.StringIO()
        ps = pstats.Stats(self._cpu_profiler, stream=s)
        ps.strip_dirs()
        ps.sort_stats(pstats.SortKey.CUMULATIVE)
        
        # Write header
        s.write(f"Profile Duration: {duration:.2f} seconds\n")
        s.write("=" * 80 + "\n")
        s.write("Top 50 functions by cumulative time:\n")
        s.write("=" * 80 + "\n\n")
        
        ps.print_stats(50)
        
        # Also show callers for top 20
        s.write("\n" + "=" * 80 + "\n")
        s.write("Callers for top 20 functions:\n")
        s.write("=" * 80 + "\n\n")
        ps.print_callers(20)
        
        self._cpu_profiler = None
        self._profile_start_time = None
        
        logger.info("CPU profiling stopped")
        
        return s.getvalue()
    
    def get_cpu_stats_dict(self) -> dict:
        """Get CPU profiling stats as a dictionary."""
        if not self._is_profiling or not self._cpu_profiler:
            return {}
        
        ps = pstats.Stats(self._cpu_profiler)
        stats = {}
        
        for func, (cc, nc, tt, ct, callers) in ps.stats.items():
            filename, line, func_name = func
            stats[f"{filename}:{line}({func_name})"] = {
                "ncalls": nc,
                "tottime": tt,
                "cumtime": ct,
                "percall_tottime": tt / nc if nc else 0,
                "percall_cumtime": ct / nc if nc else 0,
            }
        
        return stats
    
    def profile_async_function(self, func, *args, **kwargs):
        """Profile a single async function call."""
        profiler = cProfile.Profile()
        profiler.enable()
        
        try:
            # Run the async function
            loop = asyncio.get_event_loop()
            result = loop.run_until_complete(func(*args, **kwargs))
        finally:
            profiler.disable()
        
        # Get stats
        s = io.StringIO()
        ps = pstats.Stats(profiler, stream=s)
        ps.strip_dirs()
        ps.sort_stats(pstats.SortKey.CUMULATIVE)
        ps.print_stats(50)
        
        return result, s.getvalue()
    
    def generate_flame_graph_data(self) -> dict:
        """Generate data for flame graph visualization."""
        if not self._is_profiling or not self._cpu_profiler:
            return {}
        
        ps = pstats.Stats(self._cpu_profiler)
        
        # Build call tree
        call_tree = {}
        
        for func, (cc, nc, tt, ct, callers) in ps.stats.items():
            filename, line, func_name = func
            func_id = f"{filename}:{line}({func_name})"
            
            call_tree[func_id] = {
                "name": func_name,
                "file": filename,
                "line": line,
                "cumtime": ct,
                "tottime": tt,
                "ncalls": nc,
                "children": [],
            }
            
            # Add caller relationships
            for caller_func, caller_stats in callers.items():
                caller_filename, caller_line, caller_name = caller_func
                caller_id = f"{caller_filename}:{caller_line}({caller_name})"
                
                if caller_id in call_tree:
                    call_tree[caller_id]["children"].append(func_id)
        
        return call_tree
    
    async def profile_for_duration(self, duration_seconds: int) -> str:
        """Profile for a specific duration."""
        self.start_cpu_profiling()
        
        await asyncio.sleep(duration_seconds)
        
        return self.stop_cpu_profiling()


# Memory profiling decorators

def profile_memory(func):
    """Decorator for memory profiling (requires memory_profiler)."""
    if not MEMORY_PROFILER_AVAILABLE:
        logger.warning("memory_profiler not available, memory profiling disabled")
        return func
    
    return memory_profile(func)


# Global profiler instance
_profiler: Optional[Profiler] = None


def get_profiler() -> Profiler:
    """Get or create the global profiler."""
    global _profiler
    if _profiler is None:
        _profiler = Profiler()
    return _profiler
