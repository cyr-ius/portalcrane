/**
 * Portalcrane - JobsListComponent
 *
 * Displays the staging pipeline job list.
 *
 * The polling loop is owned by JobService (singleton) so the job list persists
 * when the user navigates away from and back to the Staging page.
 * This component calls startPolling() on init (idempotent) and does NOT own
 * or manage any polling subscription itself.
 */
import { Component, inject, OnInit } from "@angular/core";
import { JobService } from "../../../core/services/job.service";
import { JobDetailComponent } from "../job-detail/job-detail.component";

@Component({
  selector: "app-jobs-list",
  imports: [JobDetailComponent],
  templateUrl: "./jobs-list.component.html",
  styleUrl: "./jobs-list.component.css",
})
export class JobsListComponent implements OnInit {
  readonly jobSvc = inject(JobService);

  ngOnInit(): void {
    // Ensure the polling loop is running.
    // Idempotent: does nothing when already active (e.g. returning to this page).
    this.jobSvc.startPolling();
  }
}
