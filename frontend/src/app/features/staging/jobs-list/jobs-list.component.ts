import { Component, DestroyRef, inject, OnInit } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { switchMap, timer } from "rxjs";
import { JobService } from "../../../core/services/job.service";
import { JobDetailComponent } from "../job-detail/job-detail.component";


@Component({
  selector: "app-jobs-list",
  imports: [JobDetailComponent],
  templateUrl: "./jobs-list.component.html",
  styleUrl: "./jobs-list.component.css",
})
export class JobsListComponent implements OnInit {
  private destroyRef = inject(DestroyRef);
  jobSvc = inject(JobService)

  ngOnInit(): void {
    this.startJobsAutoRefresh();
  }

  private startJobsAutoRefresh(): void {
    timer(200, 3000)
      .pipe(
        switchMap(() => this.jobSvc.listJobs()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((jobs) => this.jobSvc.setJobs(jobs));
  }

}
